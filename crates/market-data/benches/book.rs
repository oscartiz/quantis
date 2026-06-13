//! Order-book ladder benchmarks: contiguous `Vec` vs `BTreeMap`.
//!
//! Two workloads: full-snapshot application (Hyperliquid's actual feed shape)
//! and single-level updates (the delta shape other venues use). Numbers feed
//! ADR-002's appendix; the production choice follows the measurement.

use criterion::{Criterion, Throughput, criterion_group, criterion_main};
use quantis_core::events::{L2Snapshot, Level};
use quantis_core::types::{Px, Qty, Side, TsNanos};
use quantis_market_data::book::{BTreeBook, OrderBook};
use std::hint::black_box;

const DEPTH: usize = 20;
const TICK: i64 = 50_000_000; // $0.5

fn snapshot(mid_raw: i64, i: u32) -> L2Snapshot {
    let mut bids = Vec::with_capacity(DEPTH);
    let mut asks = Vec::with_capacity(DEPTH);
    for lvl in 0..DEPTH as i64 {
        let qty = Qty::from_raw(10_000_000 + (i as i64 * 7 + lvl * 13) % 400_000_000);
        bids.push(Level {
            px: Px::from_raw(mid_raw - TICK / 2 - lvl * TICK),
            qty,
            n_orders: 1 + (i % 7),
        });
        asks.push(Level {
            px: Px::from_raw(mid_raw + TICK / 2 + lvl * TICK),
            qty,
            n_orders: 1 + (i % 5),
        });
    }
    L2Snapshot {
        exch_ts: TsNanos::from_millis(i64::from(i)),
        recv_ts: TsNanos::from_millis(i64::from(i) + 1),
        bids,
        asks,
    }
}

fn snapshots() -> Vec<L2Snapshot> {
    let mut mid = 100_000 * 100_000_000i64;
    (0..1_000u32)
        .map(|i| {
            mid += ((i as i64 * 31) % 21 - 10) * TICK;
            snapshot(mid, i)
        })
        .collect()
}

/// (side, px, qty) level updates around a fixed mid, ~20% removals.
fn level_updates() -> Vec<(Side, Px, Qty)> {
    let mid = 100_000 * 100_000_000i64;
    (0..10_000u32)
        .map(|i| {
            let side = if i % 2 == 0 { Side::Buy } else { Side::Sell };
            let offset = (TICK / 2) + ((i as i64 * 17) % 40) * TICK;
            let px = Px::from_raw(if side == Side::Buy {
                mid - offset
            } else {
                mid + offset
            });
            let qty = if i % 5 == 0 {
                Qty::ZERO
            } else {
                Qty::from_raw(10_000_000 + (i as i64 * 13) % 200_000_000)
            };
            (side, px, qty)
        })
        .collect()
}

fn bench_snapshots(c: &mut Criterion) {
    let snaps = snapshots();
    let mut group = c.benchmark_group("book_apply_snapshot_20lvl");
    group.throughput(Throughput::Elements(snaps.len() as u64));
    group.bench_function("vec", |b| {
        let mut book = OrderBook::new();
        b.iter(|| {
            for s in &snaps {
                book.apply_snapshot(black_box(s));
            }
        });
    });
    group.bench_function("btree", |b| {
        let mut book = BTreeBook::new();
        b.iter(|| {
            for s in &snaps {
                book.apply_snapshot(black_box(s));
            }
        });
    });
    group.finish();
}

fn bench_level_updates(c: &mut Criterion) {
    let updates = level_updates();
    let warm = snapshot(100_000 * 100_000_000, 0);
    let mut group = c.benchmark_group("book_apply_level");
    group.throughput(Throughput::Elements(updates.len() as u64));
    group.bench_function("vec", |b| {
        let mut book = OrderBook::new();
        book.apply_snapshot(&warm);
        b.iter(|| {
            for (side, px, qty) in &updates {
                book.apply_level(black_box(*side), *px, *qty, 1);
            }
        });
    });
    group.bench_function("btree", |b| {
        let mut book = BTreeBook::new();
        book.apply_snapshot(&warm);
        b.iter(|| {
            for (side, px, qty) in &updates {
                book.apply_level(black_box(*side), *px, *qty, 1);
            }
        });
    });
    group.finish();
}

criterion_group!(benches, bench_snapshots, bench_level_updates);
criterion_main!(benches);
