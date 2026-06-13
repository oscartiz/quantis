//! Seeded, hashed results artifacts.
//!
//! Every backtest emits one JSON artifact split into two sections:
//!
//! - `deterministic`: seed, config hash, data hash, and integer-exact metrics.
//!   Bit-identical for identical inputs on any platform; its SHA-256 is the
//!   `determinism_hash` asserted by the CI smoke test.
//! - `runtime`: git SHA, wall-clock context, and measured performance —
//!   honest provenance that legitimately varies between runs, excluded from
//!   the hash.
//!
//! Hash canonicalization: `serde_json` serializes struct fields in
//! declaration order, so the hash is stable for a fixed `schema_version`;
//! any field change must bump it.

use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

use quantis_core::hash::sha256_hex;
use serde::Serialize;

use crate::engine::RunSummary;

/// Current artifact schema version.
pub const SCHEMA_VERSION: u32 = 1;

/// The full results artifact.
#[derive(Debug, Serialize)]
pub struct ResultsArtifact {
    /// Schema version of this artifact.
    pub schema_version: u32,
    /// SHA-256 over the canonical JSON of `deterministic`.
    pub determinism_hash: String,
    /// Reproducible section (hashed).
    pub deterministic: DeterministicSection,
    /// Provenance and measured performance (not hashed).
    pub runtime: RuntimeSection,
}

/// Everything that must be identical across runs with identical inputs.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct DeterministicSection {
    /// Master seed from config.
    pub seed: u64,
    /// SHA-256 of the raw config file bytes.
    pub config_sha256: String,
    /// Data file path as configured (relative; stable across machines).
    pub data_file: String,
    /// SHA-256 of the data file.
    pub data_sha256: String,
    /// Instrument symbol.
    pub instrument: String,
    /// Strategy name.
    pub strategy: String,
    /// Integer-exact run metrics.
    pub metrics: MetricsSection,
}

/// Deterministic metrics; fixed-point values as exact decimal strings.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct MetricsSection {
    /// Total events processed.
    pub events: u64,
    /// L2 snapshots.
    pub snapshots: u64,
    /// Market-data trade prints.
    pub md_trades: u64,
    /// Fills executed.
    pub fills: u64,
    /// Quantity that found no visible liquidity.
    pub unfilled_qty: String,
    /// Quantity of orders still in flight at end of data.
    pub expired_qty: String,
    /// Total traded notional.
    pub volume: String,
    /// Total trading fees paid.
    pub fees: String,
    /// Total funding paid (positive) or received (negative).
    pub funding_paid: String,
    /// Final equity (mark-to-mid).
    pub final_equity: String,
    /// Final equity minus initial cash.
    pub net_pnl: String,
    /// Max peak-to-trough equity drawdown.
    pub max_drawdown: String,
    /// Position at end of run.
    pub end_position: String,
    /// Crossed-book snapshots observed (data quality signal).
    pub book_crossed: u64,
    /// Exchange-timestamp regressions observed.
    pub book_ts_regressions: u64,
}

/// Non-deterministic provenance and performance.
#[derive(Debug, Serialize)]
pub struct RuntimeSection {
    /// Git SHA of the build/working tree (or "unknown").
    pub git_sha: String,
    /// Artifact creation time, ms since the Unix epoch.
    pub created_unix_ms: i64,
    /// `debug` or `release`.
    pub build_profile: &'static str,
    /// Events per second through the full loop.
    pub events_per_sec: f64,
    /// Per-event p50, nanoseconds.
    pub p50_ns: i64,
    /// Per-event p95, nanoseconds.
    pub p95_ns: i64,
    /// Per-event p99, nanoseconds.
    pub p99_ns: i64,
    /// Worst event, nanoseconds.
    pub max_ns: i64,
}

impl MetricsSection {
    /// Build from a run summary.
    pub fn from_summary(s: &RunSummary) -> Self {
        Self {
            events: s.counts.events,
            snapshots: s.counts.snapshots,
            md_trades: s.counts.md_trades,
            fills: s.account.fills,
            unfilled_qty: s.account.unfilled_qty.to_string(),
            expired_qty: s.account.expired_qty.to_string(),
            volume: s.account.volume.to_string(),
            fees: s.account.fees.to_string(),
            funding_paid: s.account.funding_paid.to_string(),
            final_equity: s.account.final_equity.to_string(),
            net_pnl: s.account.net_pnl.to_string(),
            max_drawdown: s.account.max_drawdown.to_string(),
            end_position: s.account.end_position.to_string(),
            book_crossed: s.book_stats.crossed,
            book_ts_regressions: s.book_stats.ts_regressions,
        }
    }
}

impl ResultsArtifact {
    /// Assemble an artifact, computing the determinism hash.
    pub fn new(deterministic: DeterministicSection, runtime: RuntimeSection) -> Self {
        let canonical =
            serde_json::to_vec(&deterministic).expect("artifact serialization cannot fail");
        Self {
            schema_version: SCHEMA_VERSION,
            determinism_hash: sha256_hex(&canonical),
            deterministic,
            runtime,
        }
    }

    /// Write `backtest-<created>-<hash8>.json` into `dir`; returns the path.
    pub fn write_to_dir(&self, dir: &Path) -> io::Result<PathBuf> {
        std::fs::create_dir_all(dir)?;
        let name = format!(
            "backtest-{}-{}.json",
            self.runtime.created_unix_ms,
            &self.determinism_hash[..8]
        );
        let path = dir.join(name);
        std::fs::write(
            &path,
            serde_json::to_string_pretty(self).expect("artifact serialization cannot fail"),
        )?;
        Ok(path)
    }
}

/// Best-effort git SHA: `QUANTIS_GIT_SHA` env, then `git rev-parse`, then
/// `"unknown"`. Never fails — provenance should not block a run.
pub fn current_git_sha() -> String {
    if let Ok(sha) = std::env::var("QUANTIS_GIT_SHA") {
        return sha;
    }
    Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_owned())
        .unwrap_or_else(|| "unknown".to_owned())
}

/// The build profile this binary was compiled with.
pub const fn build_profile() -> &'static str {
    if cfg!(debug_assertions) {
        "debug"
    } else {
        "release"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::{EngineParams, run};
    use crate::fill::FillParams;
    use crate::strategy::SmaCross;
    use crate::synthetic::synthetic_events;

    fn deterministic_section() -> DeterministicSection {
        let events = synthetic_events(42, 2_000);
        let mut strat = SmaCross::new(20, 80, "0.01".parse().unwrap());
        let summary = run(
            events.into_iter(),
            &mut strat,
            &EngineParams {
                initial_cash: "100000".parse().unwrap(),
                fill: FillParams {
                    taker_fee_ppm: 450,
                    maker_fee_ppm: 150,
                },
                latency_ms: 50,
                funding_interval_ms: 0,
                funding_rate_ppm: 0,
            },
        );
        DeterministicSection {
            seed: 42,
            config_sha256: "cfg".into(),
            data_file: "synthetic".into(),
            data_sha256: "data".into(),
            instrument: "BTC".into(),
            strategy: "sma_cross".into(),
            metrics: MetricsSection::from_summary(&summary),
        }
    }

    #[test]
    fn determinism_hash_is_stable_across_runs() {
        let a = ResultsArtifact::new(
            deterministic_section(),
            RuntimeSection {
                git_sha: "abc".into(),
                created_unix_ms: 1,
                build_profile: "debug",
                events_per_sec: 1.0,
                p50_ns: 1,
                p95_ns: 2,
                p99_ns: 3,
                max_ns: 4,
            },
        );
        let b = ResultsArtifact::new(
            deterministic_section(),
            RuntimeSection {
                git_sha: "different".into(),
                created_unix_ms: 999,
                build_profile: "release",
                events_per_sec: 9.9,
                p50_ns: 9,
                p95_ns: 9,
                p99_ns: 9,
                max_ns: 9,
            },
        );
        // runtime differences must not move the determinism hash
        assert_eq!(a.determinism_hash, b.determinism_hash);
        assert_eq!(a.deterministic, b.deterministic);
    }
}
