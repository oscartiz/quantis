//! Order-book maintenance with integrity accounting.
//!
//! Hyperliquid's l2Book feed pushes full snapshots, so the production book
//! ([`OrderBook`]) is a pair of contiguous vectors rebuilt per snapshot —
//! cache-friendly and allocation-free after warm-up. A `BTreeMap` ladder
//! ([`BTreeBook`]) is kept alongside it for the level-update (delta) workload
//! that other venues use; `benches/book.rs` measures both and ADR-002 records
//! the numbers rather than asserting taste.

use std::collections::BTreeMap;

use quantis_core::events::{L2Snapshot, Level};
use quantis_core::types::{Px, Qty, Side, TsNanos};

/// Production order book: contiguous ladders, snapshot-oriented.
///
/// Invariants maintained: bids sorted descending, asks ascending, no
/// non-positive quantities. Violations in incoming data are repaired and
/// **counted**, never silently absorbed — the counters are part of replay
/// integrity reports.
#[derive(Debug, Default, Clone)]
pub struct OrderBook {
    bids: Vec<Level>,
    asks: Vec<Level>,
    last_exch_ts: TsNanos,
    stats: BookStats,
}

/// Integrity and volume counters for a book.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct BookStats {
    /// Snapshots applied.
    pub snapshots: u64,
    /// Individual level updates applied.
    pub level_updates: u64,
    /// Snapshots that arrived crossed (best bid >= best ask).
    pub crossed: u64,
    /// Incoming levels dropped for non-positive quantity.
    pub bad_qty_levels: u64,
    /// Snapshots whose ladders arrived unsorted and were re-sorted.
    pub unsorted: u64,
    /// Events whose exchange timestamp went backwards.
    pub ts_regressions: u64,
}

impl OrderBook {
    /// An empty book.
    pub fn new() -> Self {
        Self::default()
    }

    /// Replace the book contents with a snapshot, enforcing invariants.
    pub fn apply_snapshot(&mut self, snap: &L2Snapshot) {
        if snap.exch_ts < self.last_exch_ts {
            self.stats.ts_regressions += 1;
        }
        self.last_exch_ts = snap.exch_ts;

        self.stats.bad_qty_levels += copy_levels(&mut self.bids, &snap.bids);
        self.stats.bad_qty_levels += copy_levels(&mut self.asks, &snap.asks);

        if !self.bids.is_sorted_by(|a, b| a.px > b.px) {
            self.bids.sort_by_key(|l| std::cmp::Reverse(l.px));
            self.stats.unsorted += 1;
        }
        if !self.asks.is_sorted_by(|a, b| a.px < b.px) {
            self.asks.sort_by_key(|l| l.px);
            self.stats.unsorted += 1;
        }

        if let (Some(bb), Some(ba)) = (self.best_bid(), self.best_ask())
            && bb.px >= ba.px
        {
            self.stats.crossed += 1;
        }
        self.stats.snapshots += 1;
    }

    /// Set the size at a single price level; `qty <= 0` removes the level.
    /// This is the delta-feed path (unused by Hyperliquid's snapshot feed,
    /// exercised by the backtester and benchmarks).
    pub fn apply_level(&mut self, side: Side, px: Px, qty: Qty, n_orders: u32) {
        let ladder = match side {
            Side::Buy => &mut self.bids,
            Side::Sell => &mut self.asks,
        };
        // Bids are descending, asks ascending: search with side-aware order.
        let pos = match side {
            Side::Buy => ladder.binary_search_by(|l| px.cmp(&l.px)),
            Side::Sell => ladder.binary_search_by(|l| l.px.cmp(&px)),
        };
        match pos {
            Ok(i) => {
                if qty.raw() <= 0 {
                    ladder.remove(i);
                } else {
                    ladder[i].qty = qty;
                    ladder[i].n_orders = n_orders;
                }
            }
            Err(i) => {
                if qty.raw() > 0 {
                    ladder.insert(i, Level { px, qty, n_orders });
                }
            }
        }
        self.stats.level_updates += 1;
    }

    /// Best bid level, if any.
    pub fn best_bid(&self) -> Option<Level> {
        self.bids.first().copied()
    }

    /// Best ask level, if any.
    pub fn best_ask(&self) -> Option<Level> {
        self.asks.first().copied()
    }

    /// Integer midpoint of the touch, if both sides exist.
    pub fn mid(&self) -> Option<Px> {
        Some(Px::mid(self.best_bid()?.px, self.best_ask()?.px))
    }

    /// Bid ladder, best first.
    pub fn bids(&self) -> &[Level] {
        &self.bids
    }

    /// Ask ladder, best first.
    pub fn asks(&self) -> &[Level] {
        &self.asks
    }

    /// Exchange timestamp of the last applied event.
    pub fn last_exch_ts(&self) -> TsNanos {
        self.last_exch_ts
    }

    /// Integrity and volume counters.
    pub fn stats(&self) -> BookStats {
        self.stats
    }
}

/// Copy levels into `dst`, dropping non-positive quantities; returns the
/// number dropped. Reuses `dst`'s allocation.
fn copy_levels(dst: &mut Vec<Level>, src: &[Level]) -> u64 {
    dst.clear();
    let mut dropped = 0;
    for l in src {
        if l.qty.raw() > 0 {
            dst.push(*l);
        } else {
            dropped += 1;
        }
    }
    dropped
}

/// `BTreeMap`-laddered book for the delta-update workload; benchmark
/// comparison subject for [`OrderBook`] (see ADR-002 appendix).
#[derive(Debug, Default)]
pub struct BTreeBook {
    bids: BTreeMap<i64, (Qty, u32)>,
    asks: BTreeMap<i64, (Qty, u32)>,
}

impl BTreeBook {
    /// An empty book.
    pub fn new() -> Self {
        Self::default()
    }

    /// Replace the book contents with a snapshot.
    pub fn apply_snapshot(&mut self, snap: &L2Snapshot) {
        self.bids.clear();
        self.asks.clear();
        for l in &snap.bids {
            if l.qty.raw() > 0 {
                self.bids.insert(l.px.raw(), (l.qty, l.n_orders));
            }
        }
        for l in &snap.asks {
            if l.qty.raw() > 0 {
                self.asks.insert(l.px.raw(), (l.qty, l.n_orders));
            }
        }
    }

    /// Set the size at a single price level; `qty <= 0` removes the level.
    pub fn apply_level(&mut self, side: Side, px: Px, qty: Qty, n_orders: u32) {
        let ladder = match side {
            Side::Buy => &mut self.bids,
            Side::Sell => &mut self.asks,
        };
        if qty.raw() <= 0 {
            ladder.remove(&px.raw());
        } else {
            ladder.insert(px.raw(), (qty, n_orders));
        }
    }

    /// Best bid level, if any.
    pub fn best_bid(&self) -> Option<Level> {
        self.bids.last_key_value().map(|(px, (qty, n))| Level {
            px: Px::from_raw(*px),
            qty: *qty,
            n_orders: *n,
        })
    }

    /// Best ask level, if any.
    pub fn best_ask(&self) -> Option<Level> {
        self.asks.first_key_value().map(|(px, (qty, n))| Level {
            px: Px::from_raw(*px),
            qty: *qty,
            n_orders: *n,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lvl(px: &str, qty: &str) -> Level {
        Level {
            px: px.parse().unwrap(),
            qty: qty.parse().unwrap(),
            n_orders: 1,
        }
    }

    fn snap(bids: Vec<Level>, asks: Vec<Level>) -> L2Snapshot {
        L2Snapshot {
            exch_ts: TsNanos::from_millis(1_000),
            recv_ts: TsNanos::from_millis(1_001),
            bids,
            asks,
        }
    }

    #[test]
    fn snapshot_sets_touch_and_mid() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&snap(
            vec![lvl("100", "1"), lvl("99", "2")],
            vec![lvl("101", "1"), lvl("102", "2")],
        ));
        assert_eq!(book.best_bid().unwrap().px, "100".parse().unwrap());
        assert_eq!(book.best_ask().unwrap().px, "101".parse().unwrap());
        assert_eq!(book.mid().unwrap(), "100.5".parse().unwrap());
        assert_eq!(book.stats().crossed, 0);
    }

    #[test]
    fn crossed_and_bad_levels_are_counted_not_hidden() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&snap(
            vec![lvl("102", "1"), lvl("100", "0")],
            vec![lvl("101", "1")],
        ));
        assert_eq!(book.stats().crossed, 1);
        assert_eq!(book.stats().bad_qty_levels, 1);
        assert_eq!(book.bids().len(), 1);
    }

    #[test]
    fn unsorted_snapshots_are_repaired_and_counted() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&snap(
            vec![lvl("99", "1"), lvl("100", "1")], // wrong order for bids
            vec![lvl("101", "1")],
        ));
        assert_eq!(book.best_bid().unwrap().px, "100".parse().unwrap());
        assert_eq!(book.stats().unsorted, 1);
    }

    #[test]
    fn level_updates_insert_replace_remove_in_order() {
        let mut book = OrderBook::new();
        book.apply_level(Side::Buy, "100".parse().unwrap(), "1".parse().unwrap(), 1);
        book.apply_level(Side::Buy, "101".parse().unwrap(), "2".parse().unwrap(), 1);
        book.apply_level(Side::Buy, "99".parse().unwrap(), "3".parse().unwrap(), 1);
        assert_eq!(book.best_bid().unwrap().px, "101".parse().unwrap());
        // replace
        book.apply_level(Side::Buy, "101".parse().unwrap(), "5".parse().unwrap(), 2);
        assert_eq!(book.best_bid().unwrap().qty, "5".parse().unwrap());
        // remove best
        book.apply_level(Side::Buy, "101".parse().unwrap(), Qty::ZERO, 0);
        assert_eq!(book.best_bid().unwrap().px, "100".parse().unwrap());
        assert_eq!(book.bids().len(), 2);
    }

    #[test]
    fn vec_and_btree_books_agree_under_random_updates() {
        use rand::rngs::SmallRng;
        use rand::{Rng, SeedableRng};

        let mut rng = SmallRng::seed_from_u64(7);
        let mut vec_book = OrderBook::new();
        let mut btree_book = BTreeBook::new();
        for _ in 0..5_000 {
            let side = if rng.random_bool(0.5) {
                Side::Buy
            } else {
                Side::Sell
            };
            let base = if side == Side::Buy { 99_000 } else { 101_000 };
            let px = Px::from_raw((base + rng.random_range(0..500)) * 1_000_000);
            let qty = Qty::from_raw(rng.random_range(0..5) * 10_000_000);
            vec_book.apply_level(side, px, qty, 1);
            btree_book.apply_level(side, px, qty, 1);
            assert_eq!(vec_book.best_bid(), btree_book.best_bid());
            assert_eq!(vec_book.best_ask(), btree_book.best_ask());
        }
    }
}
