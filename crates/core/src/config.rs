//! Engine configuration: TOML on disk, validated into typed structs.
//!
//! Two rules govern this module:
//!
//! 1. **Typos cannot pass silently.** Every section rejects unknown fields,
//!    so a misspelled key is a hard error rather than a silently ignored one.
//! 2. **Fail closed.** Anything not explicitly allowed is rejected at
//!    validation time — most importantly live mainnet trading, which has no
//!    bypass flag anywhere in this codebase.

use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use thiserror::Error;

/// Errors produced while loading or validating an [`EngineConfig`].
#[derive(Debug, Error)]
pub enum ConfigError {
    /// The config file could not be read from disk.
    #[error("failed to read config file {path}: {source}")]
    Io {
        /// Path that failed to open.
        path: PathBuf,
        /// Underlying I/O error.
        #[source]
        source: std::io::Error,
    },
    /// The file was read but is not valid TOML matching the schema.
    #[error("failed to parse config: {0}")]
    Parse(#[from] toml::de::Error),
    /// `mode = "mainnet"` was requested.
    #[error(
        "mode = \"mainnet\" is disabled by design: Quantis trades paper/testnet only. \
         There is intentionally no flag to bypass this; routing real capital would \
         require a reviewed code change (see the safety posture in README.md)"
    )]
    MainnetDisabled,
    /// A venue other than Hyperliquid was configured.
    #[error("unsupported venue {0:?}: only \"hyperliquid\" is implemented")]
    UnsupportedVenue(String),
    /// A field-level constraint failed.
    #[error("invalid config: {0}")]
    Invalid(String),
}

/// Top-level engine configuration, loaded from TOML.
///
/// See `config/engine.example.toml` for a commented example; that file is
/// parsed by this module's tests, so it can never drift from the schema.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineConfig {
    /// Run-level settings: trading mode and RNG seed.
    pub engine: EngineSection,
    /// What we trade and where.
    pub instrument: InstrumentSection,
    /// Where market data is read from and written to.
    pub data: DataSection,
    /// Log verbosity and output encoding.
    pub logging: LoggingSection,
}

/// Run-level settings.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineSection {
    /// Where orders are allowed to go. `mainnet` is rejected at validation.
    pub mode: TradingMode,
    /// Master RNG seed; every stochastic component derives its stream from it.
    pub seed: u64,
}

/// Instrument identity.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstrumentSection {
    /// Trading venue. Only `"hyperliquid"` is implemented.
    pub venue: String,
    /// Exchange symbol, e.g. `"BTC"` for the BTC perpetual.
    pub symbol: String,
}

/// Data directories.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DataSection {
    /// Where the live recorder writes event logs (gitignored).
    pub capture_dir: PathBuf,
    /// Committed sample data used by the offline demo and CI smoke test.
    pub sample_dir: PathBuf,
}

/// Logging settings.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoggingSection {
    /// Minimum level to emit.
    pub level: LogLevel,
    /// Output encoding.
    pub format: LogFormat,
}

/// Order destinations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TradingMode {
    /// Simulated fills against live market data; nothing leaves the process.
    Paper,
    /// Orders are sent to the Hyperliquid testnet.
    Testnet,
    /// Recognized only so it can be rejected with a pointed error instead of
    /// a generic parse failure. Permanently disabled in this codebase.
    Mainnet,
}

impl fmt::Display for TradingMode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Paper => "paper",
            Self::Testnet => "testnet",
            Self::Mainnet => "mainnet",
        })
    }
}

/// Log verbosity levels (mirrors the `tracing` crate's levels).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogLevel {
    /// Finest-grained tracing.
    Trace,
    /// Diagnostic detail.
    Debug,
    /// Normal operation.
    Info,
    /// Recoverable anomalies.
    Warn,
    /// Failures.
    Error,
}

/// Log output encoding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogFormat {
    /// Human-readable, for development.
    Pretty,
    /// One JSON object per line, for production log collectors.
    Json,
}

impl EngineConfig {
    /// Read, parse, and validate a TOML config file.
    pub fn load(path: &Path) -> Result<Self, ConfigError> {
        let raw = fs::read_to_string(path).map_err(|source| ConfigError::Io {
            path: path.to_path_buf(),
            source,
        })?;
        let config: Self = toml::from_str(&raw)?;
        config.validate()?;
        Ok(config)
    }

    /// Enforce cross-field invariants that the type system alone cannot.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.engine.mode == TradingMode::Mainnet {
            return Err(ConfigError::MainnetDisabled);
        }
        if self.instrument.venue != "hyperliquid" {
            return Err(ConfigError::UnsupportedVenue(self.instrument.venue.clone()));
        }
        if self.instrument.symbol.trim().is_empty() {
            return Err(ConfigError::Invalid(
                "instrument.symbol must be non-empty".into(),
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn example_path() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/engine.example.toml")
    }

    fn base_toml(mode: &str) -> String {
        format!(
            r#"
[engine]
mode = "{mode}"
seed = 7

[instrument]
venue = "hyperliquid"
symbol = "BTC"

[data]
capture_dir = "data/capture"
sample_dir = "data/sample"

[logging]
level = "info"
format = "pretty"
"#
        )
    }

    #[test]
    fn example_config_is_valid() {
        let config = EngineConfig::load(&example_path()).expect("example config must validate");
        assert_eq!(config.engine.mode, TradingMode::Paper);
    }

    #[test]
    fn mainnet_is_rejected_with_pointed_error() {
        let config: EngineConfig = toml::from_str(&base_toml("mainnet")).unwrap();
        let err = config.validate().unwrap_err();
        assert!(matches!(err, ConfigError::MainnetDisabled));
        assert!(err.to_string().contains("disabled by design"));
    }

    #[test]
    fn unknown_fields_are_rejected() {
        let raw = base_toml("paper") + "spread_bps = 3\n";
        let err = toml::from_str::<EngineConfig>(&raw).unwrap_err();
        assert!(err.to_string().contains("spread_bps"));
    }

    #[test]
    fn unsupported_venue_is_rejected() {
        let raw = base_toml("paper").replace("hyperliquid", "binance");
        let config: EngineConfig = toml::from_str(&raw).unwrap();
        let err = config.validate().unwrap_err();
        assert!(matches!(err, ConfigError::UnsupportedVenue(v) if v == "binance"));
    }

    #[test]
    fn empty_symbol_is_rejected() {
        let raw = base_toml("paper").replace("\"BTC\"", "\" \"");
        let config: EngineConfig = toml::from_str(&raw).unwrap();
        assert!(matches!(
            config.validate().unwrap_err(),
            ConfigError::Invalid(_)
        ));
    }
}
