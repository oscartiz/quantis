//! The event loop: book → strategy → fills → accounting, one event at a time.
//!
//! Determinism contract: everything in [`RunSummary`] except [`Timing`] is a
//! pure function of (event stream, strategy parameters, fee schedule,
//! initial cash) — integer arithmetic only, no clocks, no RNG. Timing is
//! measured, varies run to run, and is excluded from artifact hashes.

use std::time::Instant;

use quantis_core::events::MarketEvent;
use quantis_core::stats::percentiles;
use quantis_core::types::{Cash, Qty, Side};
use quantis_market_data::book::{BookStats, OrderBook};

use crate::fill::{FillEngine, FillParams};
use crate::strategy::{Actions, Strategy};

/// Engine parameters (all from validated config).
#[derive(Debug, Clone, Copy)]
pub struct EngineParams {
    /// Starting cash in quote currency.
    pub initial_cash: Cash,
    /// Fee schedule.
    pub fill: FillParams,
}

/// Event counts by type.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct EventCounts {
    /// All events.
    pub events: u64,
    /// L2 snapshots.
    pub snapshots: u64,
    /// Market-data trade prints.
    pub md_trades: u64,
    /// Candles.
    pub candles: u64,
}

/// Deterministic accounting results (mark-to-mid; closing costs not modelled
/// in v0 — stated in ADR-004).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AccountSummary {
    /// Number of individual fills.
    pub fills: u64,
    /// Total traded notional.
    pub volume: Cash,
    /// Total fees paid.
    pub fees: Cash,
    /// Quantity that found no visible liquidity (should be zero for sane
    /// sizes; reported because silence would hide a sizing bug).
    pub unfilled_qty: Qty,
    /// Position at the end of the run.
    pub end_position: Qty,
    /// Cash + position marked at the last mid.
    pub final_equity: Cash,
    /// `final_equity - initial_cash`.
    pub net_pnl: Cash,
    /// Maximum peak-to-trough equity drawdown.
    pub max_drawdown: Cash,
}

/// Measured (non-deterministic) loop performance.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Timing {
    /// Events per wall-clock second through the full loop.
    pub events_per_sec: f64,
    /// Per-event latency percentiles, nanoseconds.
    pub p50_ns: i64,
    /// 95th percentile.
    pub p95_ns: i64,
    /// 99th percentile.
    pub p99_ns: i64,
    /// Worst event.
    pub max_ns: i64,
}

/// Everything a run produces.
#[derive(Debug, Clone)]
pub struct RunSummary {
    /// Event counts.
    pub counts: EventCounts,
    /// Deterministic accounting.
    pub account: AccountSummary,
    /// Book integrity counters.
    pub book_stats: BookStats,
    /// Measured performance (excluded from hashes).
    pub timing: Timing,
}

/// Run a strategy over an event stream.
pub fn run(
    events: impl Iterator<Item = MarketEvent>,
    strategy: &mut dyn Strategy,
    params: &EngineParams,
) -> RunSummary {
    let fill_engine = FillEngine::new(params.fill);
    let mut book = OrderBook::new();
    let mut actions = Actions::default();

    let mut counts = EventCounts::default();
    let mut cash = params.initial_cash;
    let mut position = Qty::ZERO;
    let mut fees = Cash::ZERO;
    let mut volume = Cash::ZERO;
    let mut fills: u64 = 0;
    let mut unfilled = Qty::ZERO;
    let mut peak = params.initial_cash;
    let mut max_drawdown = Cash::ZERO;
    let mut last_equity = params.initial_cash;

    let mut event_ns: Vec<i64> = Vec::with_capacity(65_536);
    let wall_start = Instant::now();

    for event in events {
        let t0 = Instant::now();
        counts.events += 1;
        match &event {
            MarketEvent::L2Snapshot(snap) => {
                counts.snapshots += 1;
                book.apply_snapshot(snap);
            }
            MarketEvent::Trade(_) => counts.md_trades += 1,
            MarketEvent::Candle(_) => counts.candles += 1,
        }

        strategy.on_event(&event, &book, position, &mut actions);
        for intent in actions.take() {
            let outcome = fill_engine.market(intent.side, intent.qty, &book, event.exch_ts());
            for fill in &outcome.fills {
                match fill.side {
                    Side::Buy => {
                        position += fill.qty;
                        cash -= fill.notional;
                    }
                    Side::Sell => {
                        position -= fill.qty;
                        cash += fill.notional;
                    }
                }
                cash -= fill.fee;
                fees += fill.fee;
                volume += fill.notional;
                fills += 1;
            }
            unfilled += outcome.unfilled;
        }

        if let Some(mid) = book.mid() {
            let equity = cash + mid.notional(position);
            if equity > peak {
                peak = equity;
            }
            let dd = peak - equity;
            if dd > max_drawdown {
                max_drawdown = dd;
            }
            last_equity = equity;
        }
        event_ns.push(t0.elapsed().as_nanos() as i64);
    }

    let wall = wall_start.elapsed();
    let p = percentiles(event_ns).unwrap_or(quantis_core::stats::Percentiles {
        count: 0,
        p50: 0,
        p95: 0,
        p99: 0,
        max: 0,
    });
    RunSummary {
        counts,
        account: AccountSummary {
            fills,
            volume,
            fees,
            unfilled_qty: unfilled,
            end_position: position,
            final_equity: last_equity,
            net_pnl: last_equity - params.initial_cash,
            max_drawdown,
        },
        book_stats: book.stats(),
        timing: Timing {
            events_per_sec: counts.events as f64 / wall.as_secs_f64().max(f64::EPSILON),
            p50_ns: p.p50,
            p95_ns: p.p95,
            p99_ns: p.p99,
            max_ns: p.max,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy::SmaCross;
    use crate::synthetic::synthetic_events;

    fn params() -> EngineParams {
        EngineParams {
            initial_cash: "100000".parse().unwrap(),
            fill: FillParams {
                taker_fee_ppm: 450,
                maker_fee_ppm: 150,
            },
        }
    }

    #[test]
    fn identical_inputs_produce_identical_deterministic_outputs() {
        let run_once = || {
            let events = synthetic_events(42, 4_000);
            let mut strat = SmaCross::new(20, 80, "0.01".parse().unwrap());
            run(events.into_iter(), &mut strat, &params())
        };
        let a = run_once();
        let b = run_once();
        assert_eq!(a.counts, b.counts);
        assert_eq!(a.account, b.account);
        assert_eq!(a.book_stats, b.book_stats);
        // timing intentionally not compared
    }

    #[test]
    fn strategy_trades_and_accounting_is_consistent() {
        let events = synthetic_events(7, 4_000);
        let mut strat = SmaCross::new(20, 80, "0.01".parse().unwrap());
        let summary = run(events.into_iter(), &mut strat, &params());

        assert!(
            summary.account.fills > 0,
            "synthetic walk should cross SMAs"
        );
        assert_eq!(summary.account.unfilled_qty, Qty::ZERO);
        // position is always one of -q, 0, +q for a target-flipping strategy
        let q = "0.01".parse::<Qty>().unwrap();
        assert!(
            [Qty::ZERO, q, -q].contains(&summary.account.end_position),
            "end position {:?}",
            summary.account.end_position
        );
        // fees are positive and bounded by volume
        assert!(summary.account.fees.raw() > 0);
        assert!(summary.account.fees < summary.account.volume);
        // equity accounting: net pnl == final - initial by construction;
        // drawdown is non-negative and at least final loss if negative pnl
        assert!(summary.account.max_drawdown.raw() >= 0);
        if summary.account.net_pnl.raw() < 0 {
            assert!(summary.account.max_drawdown >= -summary.account.net_pnl);
        }
    }
}
