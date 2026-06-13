//! `quantis backtest`: replay an event log through a strategy and emit a
//! seeded, hashed results artifact. Orchestration lives in
//! `quantis_backtest::runner` so this command and the PyO3 binding share one
//! code path and cannot produce different results.

use std::path::Path;

use quantis_backtest::runner::run_from_config;

pub fn run_backtest(config_path: &Path, expect_hash: Option<&str>) -> anyhow::Result<()> {
    let artifact = run_from_config(config_path)?;
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
