//! `quantis` — the operational command-line entry point.
//!
//! Phase 1 ships `config validate`, `record`, and `replay`; `backtest` lands
//! with the engine crate, `trade` (paper/testnet only) in Phase 4.

mod backtest;
mod record;
mod replay;
mod trade;

use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use quantis_core::config::{EngineConfig, LogFormat, LogLevel, LoggingSection};

#[derive(Debug, Parser)]
#[command(
    name = "quantis",
    version,
    about = "Quantitative research and execution engine (paper/testnet only)"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Inspect and validate configuration files.
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },
    /// Record live Hyperliquid market data to an event log.
    Record {
        /// Path to the TOML engine config.
        #[arg(long, short)]
        config: PathBuf,
        /// How long to record, in seconds.
        #[arg(long)]
        duration_secs: u64,
        /// Output file (default: capture_dir/<symbol>-<unix_ms>.qnts).
        #[arg(long)]
        out: Option<PathBuf>,
    },
    /// Replay a recorded event log: integrity report and throughput.
    Replay {
        /// Path to a .qnts event log.
        #[arg(long, short)]
        file: PathBuf,
    },
    /// Run a backtest from config and emit a hashed results artifact.
    Backtest {
        /// Path to the TOML engine config.
        #[arg(long, short)]
        config: PathBuf,
        /// Fail unless the determinism hash equals this value (CI smoke).
        #[arg(long)]
        expect_hash: Option<String>,
    },
    /// Run the paper trading engine (replay or live) with a metrics endpoint.
    Trade {
        /// Path to the TOML engine config.
        #[arg(long, short)]
        config: PathBuf,
        /// Replay a recorded event log instead of connecting live (offline demo).
        #[arg(long)]
        replay: Option<PathBuf>,
        /// How long to run when live, in seconds.
        #[arg(long, default_value_t = 60)]
        duration_secs: u64,
        /// Prometheus metrics port (0 disables the endpoint).
        #[arg(long, default_value_t = 9_898)]
        metrics_port: u16,
    },
}

#[derive(Debug, Subcommand)]
enum ConfigAction {
    /// Validate an engine TOML config file and print a summary.
    Validate {
        /// Path to the TOML config file.
        path: PathBuf,
    },
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("error: {err:#}");
            ExitCode::FAILURE
        }
    }
}

fn run(cli: Cli) -> anyhow::Result<()> {
    match cli.command {
        Command::Config {
            action: ConfigAction::Validate { path },
        } => {
            let config = EngineConfig::load(&path)?;
            println!(
                "OK: {} is valid (mode={}, venue={}, symbol={}, seed={})",
                path.display(),
                config.engine.mode,
                config.instrument.venue,
                config.instrument.symbol,
                config.engine.seed,
            );
            Ok(())
        }
        Command::Record {
            config,
            duration_secs,
            out,
        } => record::run(&config, duration_secs, out),
        Command::Replay { file } => replay::run(&file),
        Command::Backtest {
            config,
            expect_hash,
        } => backtest::run_backtest(&config, expect_hash.as_deref()),
        Command::Trade {
            config,
            replay,
            duration_secs,
            metrics_port,
        } => trade::run(&config, replay, duration_secs, metrics_port),
    }
}

/// Initialize structured logging from config. Idempotent enough for a CLI:
/// called once per process by commands that emit logs.
fn init_logging(logging: &LoggingSection) {
    let level = match logging.level {
        LogLevel::Trace => tracing::Level::TRACE,
        LogLevel::Debug => tracing::Level::DEBUG,
        LogLevel::Info => tracing::Level::INFO,
        LogLevel::Warn => tracing::Level::WARN,
        LogLevel::Error => tracing::Level::ERROR,
    };
    let builder = tracing_subscriber::fmt().with_max_level(level);
    match logging.format {
        LogFormat::Pretty => builder.init(),
        LogFormat::Json => builder.json().init(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_accepts_example_config() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../config/engine.example.toml"
        );
        let cli = Cli::try_parse_from(["quantis", "config", "validate", path]).unwrap();
        run(cli).unwrap();
    }

    #[test]
    fn trade_requires_a_config() {
        // `trade` now needs --config; parsing without it must fail.
        assert!(Cli::try_parse_from(["quantis", "trade"]).is_err());
    }
}
