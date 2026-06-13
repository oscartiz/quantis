//! Deterministic synthetic event streams for tests and benchmarks.
//!
//! **Not market data.** Synthetic streams exist to exercise the engine and
//! measure its throughput; they are never used to support any claim about
//! strategy performance. The committed sample under `data/sample/` is real
//! recorded Hyperliquid data, with provenance.

use quantis_core::events::{L2Snapshot, Level, MarketEvent, Trade};
use quantis_core::types::{Px, Qty, Side, TsNanos};
use rand::Rng;
use rand_chacha::ChaCha8Rng;
use rand_chacha::rand_core::SeedableRng;

/// One price tick of the synthetic instrument: $0.5 in raw 1e8 units.
const TICK: i64 = 50_000_000;
/// Book depth per side.
const DEPTH: usize = 10;

/// Generate a seeded random-walk stream of `n_snapshots` L2 snapshots with a
/// trade print every fifth snapshot. Identical (seed, n) → identical stream.
pub fn synthetic_events(seed: u64, n_snapshots: usize) -> Vec<MarketEvent> {
    let mut rng = ChaCha8Rng::seed_from_u64(seed);
    let mut events = Vec::with_capacity(n_snapshots + n_snapshots / 5);
    // Start at $100,000.
    let mut mid: i64 = 100_000 * 100_000_000;
    let mut ts_ms: i64 = 1_700_000_000_000;

    for i in 0..n_snapshots {
        // Random walk: up to ±10 ticks per step.
        mid += rng.random_range(-10..=10) * TICK;
        let half_spread = rng.random_range(1..=3) * TICK / 2;

        let mut bids = Vec::with_capacity(DEPTH);
        let mut asks = Vec::with_capacity(DEPTH);
        for lvl in 0..DEPTH {
            let offset = half_spread + (lvl as i64) * TICK;
            let qty = Qty::from_raw(rng.random_range(10_000_000..=500_000_000)); // 0.1..5
            bids.push(Level {
                px: Px::from_raw(mid - offset),
                qty,
                n_orders: rng.random_range(1..=8),
            });
            asks.push(Level {
                px: Px::from_raw(mid + offset),
                qty,
                n_orders: rng.random_range(1..=8),
            });
        }
        let exch_ts = TsNanos::from_millis(ts_ms);
        let recv_ts = TsNanos::from_millis(ts_ms + 2);
        events.push(MarketEvent::L2Snapshot(L2Snapshot {
            exch_ts,
            recv_ts,
            bids,
            asks,
        }));

        if i.is_multiple_of(5) {
            let side = if rng.random_bool(0.5) {
                Side::Buy
            } else {
                Side::Sell
            };
            events.push(MarketEvent::Trade(Trade {
                px: Px::from_raw(mid + half_spread * side.sign()),
                qty: Qty::from_raw(rng.random_range(1_000_000..=100_000_000)),
                side,
                exch_ts,
                recv_ts,
                tid: i as u64,
            }));
        }
        ts_ms += 500;
    }
    events
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_seed_same_stream() {
        assert_eq!(synthetic_events(1, 200), synthetic_events(1, 200));
    }

    #[test]
    fn different_seed_different_stream() {
        assert_ne!(synthetic_events(1, 200), synthetic_events(2, 200));
    }
}
