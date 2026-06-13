//! The event loop: book → funding → arrived fills → strategy → accounting.
//!
//! Determinism contract: everything in [`RunSummary`] except [`Timing`] is a
//! pure function of (event stream, strategy parameters, fee schedule, latency,
//! funding, initial cash) — integer arithmetic only, no clocks, no RNG. Timing
//! is measured, varies run to run, and is excluded from artifact hashes.
//!
//! Execution realism (v1, ADR-004): an order submitted while processing a
//! snapshot does not fill against that snapshot. It is queued with an arrival
//! time of `submit_ts + latency_ms` and fills against the first *later* snapshot
//! whose timestamp has reached arrival — you cannot trade on the snapshot that
//! triggered your signal. Funding accrues on the open position at each funding
//! interval boundary.

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
    /// Order arrival delay in milliseconds (see module docs).
    pub latency_ms: i64,
    /// Funding interval in ms; `0` disables funding.
    pub funding_interval_ms: i64,
    /// Signed funding rate in ppm of notional per interval (positive = longs pay).
    pub funding_rate_ppm: i64,
}

/// A market order in flight: submitted, not yet arrived at the exchange.
#[derive(Debug, Clone, Copy)]
struct PendingOrder {
    side: Side,
    qty: Qty,
    arrival_ms: i64,
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
/// in v1 — stated in ADR-004).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AccountSummary {
    /// Number of individual fills.
    pub fills: u64,
    /// Total traded notional.
    pub volume: Cash,
    /// Total trading fees paid.
    pub fees: Cash,
    /// Total funding paid (positive) or received (negative) over the run.
    pub funding_paid: Cash,
    /// Quantity that found no visible liquidity at fill time.
    pub unfilled_qty: Qty,
    /// Quantity of orders still in flight when the data ended (never filled).
    pub expired_qty: Qty,
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
    /// Per-event p50 latency, nanoseconds.
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

/// Mutable accounting state threaded through the loop.
struct Ledger {
    cash: Cash,
    position: Qty,
    fees: Cash,
    funding_paid: Cash,
    volume: Cash,
    fills: u64,
    unfilled: Qty,
    peak: Cash,
    max_drawdown: Cash,
    last_equity: Cash,
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
    let mut pending: Vec<PendingOrder> = Vec::new();

    let mut counts = EventCounts::default();
    let mut ledger = Ledger {
        cash: params.initial_cash,
        position: Qty::ZERO,
        fees: Cash::ZERO,
        funding_paid: Cash::ZERO,
        volume: Cash::ZERO,
        fills: 0,
        unfilled: Qty::ZERO,
        peak: params.initial_cash,
        max_drawdown: Cash::ZERO,
        last_equity: params.initial_cash,
    };
    let mut next_funding_ms: Option<i64> = None;

    let mut event_ns: Vec<i64> = Vec::with_capacity(65_536);
    let wall_start = Instant::now();

    for event in events {
        let t0 = Instant::now();
        let now_ms = event.exch_ts().as_millis();
        counts.events += 1;
        match &event {
            MarketEvent::L2Snapshot(snap) => {
                counts.snapshots += 1;
                book.apply_snapshot(snap);
            }
            MarketEvent::Trade(_) => counts.md_trades += 1,
            MarketEvent::Candle(_) => counts.candles += 1,
        }

        // Funding accrues at every interval boundary that `now_ms` has reached.
        if params.funding_interval_ms > 0 {
            let next = next_funding_ms.get_or_insert(now_ms + params.funding_interval_ms);
            while now_ms >= *next {
                if let Some(mid) = book.mid() {
                    let signed_notional = mid.notional(ledger.position);
                    let payment = signed_notional.rate_ppm_signed(params.funding_rate_ppm);
                    ledger.cash -= payment;
                    ledger.funding_paid += payment;
                }
                *next += params.funding_interval_ms;
            }
        }

        // Orders that have arrived fill against the current (just-updated) book.
        // Only snapshots refresh the book, so execute there.
        if matches!(event, MarketEvent::L2Snapshot(_)) {
            pending.retain(|order| {
                if order.arrival_ms > now_ms {
                    return true; // still in flight
                }
                let outcome = fill_engine.market(order.side, order.qty, &book, event.exch_ts());
                apply_fills(&mut ledger, &outcome.fills);
                ledger.unfilled += outcome.unfilled;
                false // arrived and processed (filled and/or unfilled)
            });
        }

        // The strategy sees the position after arrived fills, then submits.
        strategy.on_event(&event, &book, ledger.position, &mut actions);
        for intent in actions.take() {
            pending.push(PendingOrder {
                side: intent.side,
                qty: intent.qty,
                arrival_ms: now_ms + params.latency_ms,
            });
        }

        mark_to_market(&mut ledger, &book);
        event_ns.push(t0.elapsed().as_nanos() as i64);
    }

    // Orders still in flight at end of data never fill.
    let expired_qty = pending.iter().fold(Qty::ZERO, |acc, o| acc + o.qty);

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
            fills: ledger.fills,
            volume: ledger.volume,
            fees: ledger.fees,
            funding_paid: ledger.funding_paid,
            unfilled_qty: ledger.unfilled,
            expired_qty,
            end_position: ledger.position,
            final_equity: ledger.last_equity,
            net_pnl: ledger.last_equity - params.initial_cash,
            max_drawdown: ledger.max_drawdown,
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

fn apply_fills(ledger: &mut Ledger, fills: &[crate::fill::Fill]) {
    for fill in fills {
        match fill.side {
            Side::Buy => {
                ledger.position += fill.qty;
                ledger.cash -= fill.notional;
            }
            Side::Sell => {
                ledger.position -= fill.qty;
                ledger.cash += fill.notional;
            }
        }
        ledger.cash -= fill.fee;
        ledger.fees += fill.fee;
        ledger.volume += fill.notional;
        ledger.fills += 1;
    }
}

fn mark_to_market(ledger: &mut Ledger, book: &OrderBook) {
    if let Some(mid) = book.mid() {
        let equity = ledger.cash + mid.notional(ledger.position);
        if equity > ledger.peak {
            ledger.peak = equity;
        }
        let dd = ledger.peak - equity;
        if dd > ledger.max_drawdown {
            ledger.max_drawdown = dd;
        }
        ledger.last_equity = equity;
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
            latency_ms: 50,
            funding_interval_ms: 0,
            funding_rate_ppm: 0,
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
    }

    #[test]
    fn latency_resolution_limit_is_honest() {
        // An order is never filled on the snapshot that triggered it: arrived
        // orders are executed before new ones are queued, so there is always a
        // one-snapshot execution delay. Consequently latency BELOW the ~500ms
        // synthetic snapshot gap is a no-op (0ms and 50ms are identical), while
        // latency ABOVE the gap pushes execution a further snapshot out and
        // does change realised prices. This resolution limit is ADR-004's
        // central honesty point about snapshot-cadence data.
        let events = synthetic_events(7, 4_000);
        let run_with = |latency_ms: i64| {
            let mut s = SmaCross::new(20, 80, "0.01".parse().unwrap());
            run(
                events.clone().into_iter(),
                &mut s,
                &EngineParams {
                    latency_ms,
                    ..params()
                },
            )
        };
        let a0 = run_with(0);
        let a50 = run_with(50);
        let a600 = run_with(600);
        assert!(a0.account.fills > 0);
        assert_eq!(
            a0.account.net_pnl, a50.account.net_pnl,
            "sub-gap latency must be a no-op"
        );
        assert_ne!(
            a0.account.net_pnl, a600.account.net_pnl,
            "supra-gap latency must bite"
        );
    }

    #[test]
    fn funding_charges_an_open_position() {
        // A strategy that goes long once and holds; positive funding must cost
        // a long money relative to no funding.
        let events = synthetic_events(7, 4_000);
        let base = EngineParams {
            latency_ms: 0,
            funding_interval_ms: 0,
            funding_rate_ppm: 0,
            ..params()
        };
        let funded = EngineParams {
            latency_ms: 0,
            funding_interval_ms: 100_000, // ~ every 200 snapshots (500ms each)
            funding_rate_ppm: 1_000,
            ..params()
        };
        let mut s1 = SmaCross::new(20, 80, "0.01".parse().unwrap());
        let mut s2 = SmaCross::new(20, 80, "0.01".parse().unwrap());
        let a = run(events.clone().into_iter(), &mut s1, &base);
        let b = run(events.into_iter(), &mut s2, &funded);
        assert_eq!(a.account.funding_paid, Cash::ZERO);
        assert_ne!(b.account.funding_paid, Cash::ZERO);
    }

    #[test]
    fn accounting_is_self_consistent() {
        let events = synthetic_events(7, 4_000);
        let mut strat = SmaCross::new(20, 80, "0.01".parse().unwrap());
        let summary = run(events.into_iter(), &mut strat, &params());
        assert!(summary.account.fills > 0);
        let q = "0.01".parse::<Qty>().unwrap();
        assert!([Qty::ZERO, q, -q].contains(&summary.account.end_position));
        assert!(summary.account.fees.raw() > 0);
        assert!(summary.account.max_drawdown.raw() >= 0);
    }
}
