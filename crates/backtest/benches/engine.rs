//! Full backtest-loop throughput on a synthetic stream: book application,
//! strategy evaluation, fills, and accounting per event. The per-event clone
//! mirrors the allocation profile of reading events from a log file.

use criterion::{Criterion, Throughput, criterion_group, criterion_main};
use quantis_backtest::engine::{EngineParams, run};
use quantis_backtest::fill::FillParams;
use quantis_backtest::strategy::SmaCross;
use quantis_backtest::synthetic::synthetic_events;
use std::hint::black_box;

fn bench_engine(c: &mut Criterion) {
    let events = synthetic_events(42, 100_000);
    let params = EngineParams {
        initial_cash: "100000".parse().unwrap(),
        fill: FillParams {
            taker_fee_ppm: 450,
            maker_fee_ppm: 150,
        },
        latency_ms: 50,
        funding_interval_ms: 3_600_000,
        funding_rate_ppm: 100,
    };
    let mut group = c.benchmark_group("backtest_engine");
    group.sample_size(10);
    group.throughput(Throughput::Elements(events.len() as u64));
    group.bench_function("full_loop_sma_120_600", |b| {
        b.iter(|| {
            let mut strat = SmaCross::new(120, 600, "0.01".parse().unwrap());
            run(
                black_box(events.iter().cloned()),
                &mut strat,
                black_box(&params),
            )
        });
    });
    group.finish();
}

criterion_group!(benches, bench_engine);
criterion_main!(benches);
