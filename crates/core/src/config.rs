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

use crate::types::{Cash, Qty};

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
    /// Market-data feed settings.
    pub market_data: MarketDataSection,
    /// Backtest settings.
    pub backtest: BacktestSection,
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

/// Market-data feed settings. Market data is public and keyless; reading the
/// mainnet feed is unrelated to (and ungated by) the trading `mode`.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MarketDataSection {
    /// WebSocket URL, e.g. `wss://api.hyperliquid.xyz/ws`.
    pub ws_url: String,
    /// Bounded feed→consumer queue size; when full, events are dropped and
    /// counted rather than buffered without limit.
    pub channel_capacity: usize,
}

/// Backtest settings.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BacktestSection {
    /// Recorded event log to replay.
    pub data_file: PathBuf,
    /// Starting cash in quote currency, as a decimal string (e.g. `"100000"`).
    pub initial_cash: String,
    /// Taker fee in parts-per-million of notional (450 = 0.045%).
    pub taker_fee_ppm: i64,
    /// Maker fee in parts-per-million of notional (150 = 0.015%).
    pub maker_fee_ppm: i64,
    /// Strategy under test.
    pub strategy: StrategySection,
}

impl BacktestSection {
    /// Parsed starting cash. Only meaningful after [`EngineConfig::validate`].
    pub fn initial_cash(&self) -> Result<Cash, ConfigError> {
        self.initial_cash
            .parse()
            .map_err(|_| ConfigError::Invalid("backtest.initial_cash must be a decimal".into()))
    }
}

/// Strategy under test. Flat schema while exactly one Rust strategy exists;
/// becomes per-strategy tables when a second one lands.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StrategySection {
    /// Which strategy to run.
    pub name: StrategyName,
    /// Fast SMA window, in L2 snapshots.
    pub fast: u32,
    /// Slow SMA window, in L2 snapshots.
    pub slow: u32,
    /// Order size in base units, as a decimal string (e.g. `"0.01"`).
    pub order_qty: String,
}

impl StrategySection {
    /// Parsed order size. Only meaningful after [`EngineConfig::validate`].
    pub fn order_qty(&self) -> Result<Qty, ConfigError> {
        self.order_qty
            .parse()
            .map_err(|_| ConfigError::Invalid("strategy.order_qty must be a decimal".into()))
    }
}

/// Known strategies.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StrategyName {
    /// SMA crossover on L2 mid-price; exists to exercise the engine
    /// deterministically, not to make money.
    SmaCross,
}

impl fmt::Display for StrategyName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::SmaCross => "sma_cross",
        })
    }
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
        if !self.market_data.ws_url.starts_with("wss://")
            && !self.market_data.ws_url.starts_with("ws://")
        {
            return Err(ConfigError::Invalid(
                "market_data.ws_url must be a ws:// or wss:// URL".into(),
            ));
        }
        if self.market_data.channel_capacity == 0 {
            return Err(ConfigError::Invalid(
                "market_data.channel_capacity must be >= 1".into(),
            ));
        }
        for (name, ppm) in [
            ("taker_fee_ppm", self.backtest.taker_fee_ppm),
            ("maker_fee_ppm", self.backtest.maker_fee_ppm),
        ] {
            if !(0..=100_000).contains(&ppm) {
                return Err(ConfigError::Invalid(format!(
                    "backtest.{name} must be in 0..=100000 (got {ppm})"
                )));
            }
        }
        if self.backtest.initial_cash()?.raw() <= 0 {
            return Err(ConfigError::Invalid(
                "backtest.initial_cash must be positive".into(),
            ));
        }
        let strat = &self.backtest.strategy;
        if strat.fast == 0 || strat.fast >= strat.slow {
            return Err(ConfigError::Invalid(format!(
                "strategy windows must satisfy 0 < fast < slow (got fast={}, slow={})",
                strat.fast, strat.slow
            )));
        }
        if strat.order_qty()?.raw() <= 0 {
            return Err(ConfigError::Invalid(
                "strategy.order_qty must be positive".into(),
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

[market_data]
ws_url = "wss://api.hyperliquid.xyz/ws"
channel_capacity = 8192

[backtest]
data_file = "data/sample/btc-sample.qnts"
initial_cash = "100000"
taker_fee_ppm = 450
maker_fee_ppm = 150

[backtest.strategy]
name = "sma_cross"
fast = 120
slow = 600
order_qty = "0.01"

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

    #[test]
    fn inverted_sma_windows_are_rejected() {
        let raw = base_toml("paper").replace("fast = 120", "fast = 600");
        let config: EngineConfig = toml::from_str(&raw).unwrap();
        let err = config.validate().unwrap_err();
        assert!(err.to_string().contains("fast < slow"), "{err}");
    }

    #[test]
    fn absurd_fees_are_rejected() {
        let raw = base_toml("paper").replace("taker_fee_ppm = 450", "taker_fee_ppm = 500000");
        let config: EngineConfig = toml::from_str(&raw).unwrap();
        assert!(matches!(
            config.validate().unwrap_err(),
            ConfigError::Invalid(_)
        ));
    }

    #[test]
    fn non_decimal_order_qty_is_rejected() {
        let raw = base_toml("paper").replace("order_qty = \"0.01\"", "order_qty = \"lots\"");
        let config: EngineConfig = toml::from_str(&raw).unwrap();
        assert!(matches!(
            config.validate().unwrap_err(),
            ConfigError::Invalid(_)
        ));
    }
}
