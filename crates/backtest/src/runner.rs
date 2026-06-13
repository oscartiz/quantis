//! Config-to-artifact orchestration, shared by the CLI and the PyO3 binding.
//!
//! Factoring this here (rather than in the CLI) is what makes "Python and the
//! CLI run the same backtest" structurally true: both call [`run_from_config`],
//! so they cannot drift. Writing artifacts to disk and printing are left to
//! callers — this function is pure orchestration returning the artifact.

use std::path::Path;

use quantis_core::config::{ConfigError, EngineConfig, StrategyName};
use quantis_core::hash::sha256_file;
use quantis_core::types::TsNanos;
use quantis_market_data::recorder::{EventReader, LogError};
use thiserror::Error;

use crate::engine::{EngineParams, run};
use crate::fill::FillParams;
use crate::report::{
    DeterministicSection, MetricsSection, ResultsArtifact, RuntimeSection, build_profile,
    current_git_sha,
};
use crate::strategy::SmaCross;

/// Errors from a config-driven backtest run.
#[derive(Debug, Error)]
pub enum RunError {
    /// Configuration failed to load or validate.
    #[error(transparent)]
    Config(#[from] ConfigError),
    /// A referenced file could not be read or hashed.
    #[error("io on {path}: {source}")]
    Io {
        /// The offending path.
        path: String,
        /// Underlying error.
        #[source]
        source: std::io::Error,
    },
    /// The event log could not be opened.
    #[error(transparent)]
    Log(#[from] LogError),
    /// The data file's instrument disagrees with the config.
    #[error("data file is for {data} but config trades {config}")]
    InstrumentMismatch {
        /// Instrument named in the log header.
        data: String,
        /// Instrument named in the config.
        config: String,
    },
    /// The event log was truncated mid-frame; a run on shortened data would be
    /// a result about the wrong dataset.
    #[error("event log {path} is truncated; re-record or re-fetch before backtesting")]
    TruncatedLog {
        /// The log path.
        path: String,
    },
}

/// Load `config_path`, replay its data file through the configured strategy,
/// and return the (unwritten) results artifact.
pub fn run_from_config(config_path: &Path) -> Result<ResultsArtifact, RunError> {
    let config = EngineConfig::load(config_path)?;

    let config_sha256 = sha256_file(config_path).map_err(|source| RunError::Io {
        path: config_path.display().to_string(),
        source,
    })?;
    let data_file = &config.backtest.data_file;
    let data_sha256 = sha256_file(data_file).map_err(|source| RunError::Io {
        path: data_file.display().to_string(),
        source,
    })?;

    let reader = EventReader::open(data_file)?;
    if reader.header().instrument != config.instrument.symbol {
        return Err(RunError::InstrumentMismatch {
            data: reader.header().instrument.clone(),
            config: config.instrument.symbol.clone(),
        });
    }

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
        latency_ms: config.backtest.latency_ms,
        funding_interval_ms: config.backtest.funding_interval_ms,
        funding_rate_ppm: config.backtest.funding_rate_ppm,
    };

    // Collect events first so a truncated log aborts the run loudly rather
    // than silently producing a result for a shortened dataset.
    let mut events = Vec::new();
    for item in reader {
        events.push(item.map_err(|_| RunError::TruncatedLog {
            path: data_file.display().to_string(),
        })?);
    }
    let summary = run(events.into_iter(), &mut strategy, &params);

    Ok(ResultsArtifact::new(
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
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo_root() -> std::path::PathBuf {
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .expect("repo root")
    }

    /// Write a temp config pointing at the sample data by absolute path, so
    /// the test needs no specific working directory (and cannot race other
    /// tests via a global CWD change).
    fn temp_config(root: &Path) -> std::path::PathBuf {
        let sample = root.join("data/sample/btc-sample.qnts");
        let toml = format!(
            r#"
[engine]
mode = "paper"
seed = 42
[instrument]
venue = "hyperliquid"
symbol = "BTC"
[data]
capture_dir = "data/capture"
sample_dir = "data/sample"
[market_data]
ws_url = "wss://api.hyperliquid.xyz/ws"
channel_capacity = 8192
[backtest]
data_file = {sample:?}
initial_cash = "100000"
taker_fee_ppm = 450
maker_fee_ppm = 150
latency_ms = 50
funding_interval_ms = 3600000
funding_rate_ppm = 100
[backtest.strategy]
name = "sma_cross"
fast = 120
slow = 600
order_qty = "0.01"
[logging]
level = "info"
format = "pretty"
"#
        );
        let dir = std::env::temp_dir().join("quantis-runner-test");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("engine.toml");
        std::fs::write(&path, toml).unwrap();
        path
    }

    #[test]
    fn config_run_is_reproducible_and_trades() {
        let cfg = temp_config(&repo_root());
        let a = run_from_config(&cfg).expect("run a");
        let b = run_from_config(&cfg).expect("run b");
        assert_eq!(a.determinism_hash, b.determinism_hash);
        assert_eq!(a.deterministic, b.deterministic);
        assert!(a.deterministic.metrics.events > 0);
        assert!(a.deterministic.metrics.fills > 0);
    }
}
