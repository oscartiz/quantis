//! `quantis record`: capture live market data to an event log.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Context;
use quantis_core::config::EngineConfig;
use quantis_core::events::MarketEvent;
use quantis_core::stats::percentiles;
use quantis_core::types::TsNanos;
use quantis_market_data::recorder::{EventWriter, LogHeader};
use quantis_market_data::ws::{FeedConfig, FeedStats, run_feed};
use tokio::sync::mpsc;
use tracing::info;

pub fn run(config_path: &Path, duration_secs: u64, out: Option<PathBuf>) -> anyhow::Result<()> {
    let config = EngineConfig::load(config_path)?;
    crate::init_logging(&config.logging);

    let out_path = out.unwrap_or_else(|| {
        config.data.capture_dir.join(format!(
            "{}-{}.qnts",
            config.instrument.symbol.to_lowercase(),
            TsNanos::now().as_millis()
        ))
    });
    if let Some(parent) = out_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("creating {}", parent.display()))?;
    }

    let header = LogHeader {
        venue: config.instrument.venue.clone(),
        instrument: config.instrument.symbol.clone(),
        created_unix_ms: TsNanos::now().as_millis(),
    };
    let mut writer = EventWriter::create(&out_path, &header)?;

    let feed_config = FeedConfig {
        url: config.market_data.ws_url.clone(),
        coin: config.instrument.symbol.clone(),
    };
    let stats = Arc::new(FeedStats::default());

    info!(out = %out_path.display(), duration_secs, "recording");

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let mut latencies_ns: Vec<i64> = Vec::new();
    let mut snapshots = 0u64;
    let mut trades = 0u64;

    runtime.block_on(async {
        let (tx, mut rx) = mpsc::channel::<MarketEvent>(config.market_data.channel_capacity);
        let feed = tokio::spawn(run_feed(feed_config, tx, Arc::clone(&stats)));
        let deadline = tokio::time::Instant::now() + Duration::from_secs(duration_secs);
        let mut last_report = std::time::Instant::now();

        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                break;
            }
            match tokio::time::timeout(remaining, rx.recv()).await {
                Ok(Some(event)) => {
                    latencies_ns.push(event.recv_ts().nanos_since(event.exch_ts()));
                    match &event {
                        MarketEvent::L2Snapshot(_) => snapshots += 1,
                        MarketEvent::Trade(_) => trades += 1,
                        MarketEvent::Candle(_) => {}
                    }
                    writer.write_event(&event)?;
                    if last_report.elapsed() > Duration::from_secs(30) {
                        let s = stats.snapshot();
                        info!(
                            written = writer.events_written(),
                            snapshots,
                            trades,
                            dropped = s.dropped,
                            reconnects = s.disconnects,
                            "recording progress"
                        );
                        last_report = std::time::Instant::now();
                    }
                }
                Ok(None) => break, // feed ended (it never should unprompted)
                Err(_) => break,   // deadline reached
            }
        }
        drop(rx);
        feed.abort();
        anyhow::Ok(())
    })?;

    let events_written = writer.events_written();
    writer.finish()?;

    let s = stats.snapshot();
    let size = std::fs::metadata(&out_path)?.len();
    let sha = quantis_core::hash::sha256_file(&out_path)?;
    println!("recorded: {}", out_path.display());
    println!("  events={events_written} (snapshots={snapshots}, trades={trades}) size={size}B");
    println!(
        "  feed: connects={} disconnects={} messages={} parse_errors={} dropped={}",
        s.connects, s.disconnects, s.messages, s.parse_errors, s.dropped
    );
    if let Some(p) = percentiles(latencies_ns) {
        println!(
            "  feed latency (recv-exch, INCLUDES clock skew): p50={:.1}ms p95={:.1}ms p99={:.1}ms",
            p.p50 as f64 / 1e6,
            p.p95 as f64 / 1e6,
            p.p99 as f64 / 1e6
        );
    }
    println!("  sha256={sha}");
    Ok(())
}
