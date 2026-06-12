//! `quantis` — the operational command-line entry point.
//!
//! Phase 0 ships `config validate`; `record`, `replay`, and `backtest` land
//! in Phase 1, `trade` (paper/testnet only) in Phase 4.

use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use quantis_core::config::EngineConfig;

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
    /// Record live Hyperliquid market data to an event log (Phase 1).
    Record,
    /// Replay a recorded event log through the engine (Phase 1).
    Replay,
    /// Run a backtest defined by a config file (Phase 1).
    Backtest,
    /// Run the paper/testnet trading engine (Phase 4).
    Trade,
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
        Command::Record => not_yet("record", 1),
        Command::Replay => not_yet("replay", 1),
        Command::Backtest => not_yet("backtest", 1),
        Command::Trade => not_yet("trade", 4),
    }
}

fn not_yet(command: &str, phase: u8) -> anyhow::Result<()> {
    anyhow::bail!("`{command}` lands in phase {phase}; see PROGRESS.md for the build order")
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
    fn stub_commands_fail_loudly_with_phase() {
        let cli = Cli::try_parse_from(["quantis", "trade"]).unwrap();
        let err = run(cli).unwrap_err();
        assert!(err.to_string().contains("phase 4"));
    }
}
