//! `quantis replay`: stream a recorded event log through the order book and
//! report integrity counters, feed latency, and apply throughput.

use std::path::Path;
use std::time::Instant;

use quantis_core::events::MarketEvent;
use quantis_core::stats::percentiles;
use quantis_market_data::book::OrderBook;
use quantis_market_data::recorder::{EventReader, LogError};

pub fn run(file: &Path) -> anyhow::Result<()> {
    let reader = EventReader::open(file)?;
    let header = reader.header().clone();
    println!(
        "log: venue={} instrument={} created_unix_ms={}",
        header.venue, header.instrument, header.created_unix_ms
    );

    let mut book = OrderBook::new();
    let mut snapshots = 0u64;
    let mut trades = 0u64;
    let mut candles = 0u64;
    let mut feed_latencies = Vec::new();
    let mut apply_ns = Vec::new();
    let mut truncated = false;

    let wall_start = Instant::now();
    for item in reader {
        let event = match item {
            Ok(e) => e,
            Err(LogError::TruncatedFrame) => {
                truncated = true;
                break;
            }
            Err(e) => return Err(e.into()),
        };
        feed_latencies.push(event.recv_ts().nanos_since(event.exch_ts()));
        match &event {
            MarketEvent::L2Snapshot(snap) => {
                let t0 = Instant::now();
                book.apply_snapshot(snap);
                apply_ns.push(t0.elapsed().as_nanos() as i64);
                snapshots += 1;
            }
            MarketEvent::Trade(_) => trades += 1,
            MarketEvent::Candle(_) => candles += 1,
        }
    }
    let wall = wall_start.elapsed();

    let total = snapshots + trades + candles;
    let stats = book.stats();
    println!("events: total={total} snapshots={snapshots} trades={trades} candles={candles}");
    println!(
        "book integrity: crossed={} unsorted={} bad_qty_levels={} ts_regressions={}",
        stats.crossed, stats.unsorted, stats.bad_qty_levels, stats.ts_regressions
    );
    println!(
        "throughput: {:.0} events/s over {:.2}s (read+apply, single thread)",
        total as f64 / wall.as_secs_f64(),
        wall.as_secs_f64()
    );
    if let Some(p) = percentiles(apply_ns) {
        println!(
            "book apply: p50={}ns p95={}ns p99={}ns max={}ns (n={})",
            p.p50, p.p95, p.p99, p.max, p.count
        );
    }
    if let Some(p) = percentiles(feed_latencies) {
        println!(
            "feed latency (recv-exch, INCLUDES clock skew): p50={:.1}ms p95={:.1}ms p99={:.1}ms",
            p.p50 as f64 / 1e6,
            p.p95 as f64 / 1e6,
            p.p99 as f64 / 1e6
        );
    }
    if truncated {
        println!(
            "WARNING: log truncated mid-frame after {total} events (recorder killed mid-write?)"
        );
    }
    Ok(())
}
