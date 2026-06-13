//! `quantis backtest`: replay an event log through a strategy and emit a
//! seeded, hashed results artifact.

use std::path::Path;

use anyhow::Context;
use quantis_backtest::engine::{EngineParams, run};
use quantis_backtest::fill::FillParams;
use quantis_backtest::report::{
    DeterministicSection, MetricsSection, ResultsArtifact, RuntimeSection, build_profile,
    current_git_sha,
};
use quantis_backtest::strategy::SmaCross;
use quantis_core::config::{EngineConfig, StrategyName};
use quantis_core::hash::sha256_file;
use quantis_core::types::TsNanos;
use quantis_market_data::recorder::EventReader;

pub fn run_backtest(config_path: &Path, expect_hash: Option<&str>) -> anyhow::Result<()> {
    let config = EngineConfig::load(config_path)?;
    crate::init_logging(&config.logging);

    let data_file = &config.backtest.data_file;
    let data_sha256 = sha256_file(data_file)
        .with_context(|| format!("hashing data file {}", data_file.display()))?;
    let config_sha256 = sha256_file(config_path)?;

    let reader = EventReader::open(data_file)?;
    let header = reader.header().clone();
    anyhow::ensure!(
        header.instrument == config.instrument.symbol,
        "data file is for {} but config trades {}",
        header.instrument,
        config.instrument.symbol
    );

    let strat_cfg = &config.backtest.strategy;
    let mut strategy = match strat_cfg.name {
        StrategyName::SmaCross => {
            SmaCross::new(strat_cfg.fast, strat_cfg.slow, strat_cfg.order_qty()?)
        }
    };
    let params = EngineParams {
        initial_cash: config.backtest.initial_cash()?,
        fill: FillParams {
            taker_fee_ppm: config.backtest.taker_fee_ppm,
            maker_fee_ppm: config.backtest.maker_fee_ppm,
        },
    };

    // Truncated logs abort the run: a backtest on silently shortened data
    // would be a result about the wrong dataset.
    let events = reader.map(|r| r.expect("event log corrupt; re-record or re-fetch"));
    let summary = run(events, &mut strategy, &params);

    let artifact = ResultsArtifact::new(
        DeterministicSection {
            seed: config.engine.seed,
            config_sha256,
            data_file: data_file.display().to_string(),
            data_sha256,
            instrument: config.instrument.symbol.clone(),
            strategy: strat_cfg.name.to_string(),
            metrics: MetricsSection::from_summary(&summary),
        },
        RuntimeSection {
            git_sha: current_git_sha(),
            created_unix_ms: TsNanos::now().as_millis(),
            build_profile: build_profile(),
            events_per_sec: summary.timing.events_per_sec,
            p50_ns: summary.timing.p50_ns,
            p95_ns: summary.timing.p95_ns,
            p99_ns: summary.timing.p99_ns,
            max_ns: summary.timing.max_ns,
        },
    );

    let out_path = artifact.write_to_dir(Path::new("results"))?;
    let m = &artifact.deterministic.metrics;
    println!(
        "backtest: {} events ({} snapshots, {} md trades) on {}",
        m.events, m.snapshots, m.md_trades, artifact.deterministic.data_file
    );
    println!(
        "  fills={} volume={} fees={} net_pnl={} max_drawdown={} end_position={}",
        m.fills, m.volume, m.fees, m.net_pnl, m.max_drawdown, m.end_position
    );
    println!(
        "  loop: {:.0} events/s, p50={}ns p95={}ns p99={}ns ({} build)",
        artifact.runtime.events_per_sec,
        artifact.runtime.p50_ns,
        artifact.runtime.p95_ns,
        artifact.runtime.p99_ns,
        artifact.runtime.build_profile
    );
    println!("  artifact: {}", out_path.display());
    println!("determinism_hash={}", artifact.determinism_hash);

    if let Some(expected) = expect_hash {
        anyhow::ensure!(
            artifact.determinism_hash == expected,
            "determinism hash mismatch:\n  expected {expected}\n  got      {got}\n\
             If a change to fill logic, data handling, or config was intentional,\n\
             update tests/smoke/expected_hash.txt in the same commit and say why.",
            got = artifact.determinism_hash
        );
        println!("hash matches expected (smoke check passed)");
    }
    Ok(())
}
