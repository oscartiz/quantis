//! `quantis trade`: run the paper engine against replayed or live data, with a
//! Prometheus metrics endpoint. Paper/testnet only — mainnet is rejected by the
//! config layer with no bypass.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Context;
use quantis_backtest::fill::FillParams;
use quantis_backtest::strategy::{Actions, SmaCross, Strategy};
use quantis_core::config::{EngineConfig, StrategyName, TradingMode};
use quantis_core::events::MarketEvent;
use quantis_execution::order::{OrderKind, OrderRequest};
use quantis_execution::{CloidGenerator, ExecReport, OrderGateway, PaperGateway, TradingMetrics};
use quantis_market_data::recorder::EventReader;
use quantis_risk::{RiskGate, RiskLimits};
use tracing::info;

/// Risk limits for the demo paper run. In a fuller system these move to config;
/// kept explicit here so the run is self-contained and the caps are visible.
fn demo_limits(initial_cash: quantis_core::types::Cash) -> RiskLimits {
    RiskLimits {
        max_position_qty: "0.10".parse().unwrap(),
        // 1.5x notional cap relative to starting cash
        max_notional: quantis_core::types::Cash::from_raw(initial_cash.raw() * 3 / 2),
        max_drawdown_frac: 0.25,
    }
}

pub fn run(
    config_path: &Path,
    replay: Option<PathBuf>,
    duration_secs: u64,
    metrics_port: u16,
) -> anyhow::Result<()> {
    let config = EngineConfig::load(config_path)?;
    crate::init_logging(&config.logging);

    // Paper or testnet only; testnet order placement is gated (Phase 4 docs).
    if config.engine.mode == TradingMode::Testnet && replay.is_none() {
        anyhow::bail!(
            "testnet live order placement requires testnet keys and is gated; \
             run with --replay for the offline paper demo (see docs/runbook.md)"
        );
    }

    let initial_cash = config.backtest.initial_cash()?;
    let risk = RiskGate::new(demo_limits(initial_cash), initial_cash)?;
    let mut gateway = PaperGateway::new(
        FillParams {
            taker_fee_ppm: config.backtest.taker_fee_ppm,
            maker_fee_ppm: config.backtest.maker_fee_ppm,
        },
        risk,
        initial_cash,
    );

    let s = &config.backtest.strategy;
    let mut strategy: SmaCross = match s.name {
        StrategyName::SmaCross => SmaCross::new(s.fast, s.slow, s.order_qty()?),
    };
    let mut cloids = CloidGenerator::new(config.engine.seed);
    let metrics = Arc::new(TradingMetrics::new());

    if metrics_port != 0 {
        serve_metrics(Arc::clone(&metrics), metrics_port)?;
        info!(
            port = metrics_port,
            "serving Prometheus metrics at /metrics"
        );
    }

    let mut actions = Actions::default();
    let mut step = |gateway: &mut PaperGateway, event: &MarketEvent| {
        gateway.on_event(event);
        metrics.on_event();
        strategy.on_event(event, gateway.book(), gateway.position(), &mut actions);
        for intent in actions.take() {
            let req = OrderRequest {
                cloid: cloids.next_id(),
                side: intent.side,
                qty: intent.qty,
                kind: OrderKind::Market,
                reduce_only: false,
            };
            if gateway.submit(req).is_ok() {
                metrics.on_submit();
            }
        }
        for report in gateway.poll_reports() {
            match report {
                ExecReport::Fill { .. } => metrics.on_fill(),
                ExecReport::Rejected { .. } => metrics.on_reject(),
                ExecReport::Cancelled { .. } => metrics.on_cancel(),
                ExecReport::Ack { .. } => {}
            }
        }
        metrics.set_position(gateway.position());
        metrics.set_equity(gateway.equity());
    };

    match replay {
        Some(path) => run_replay(&path, &mut gateway, &mut step)?,
        None => run_live(&config, duration_secs, &metrics, &mut gateway, &mut step)?,
    }

    let m = gateway.manager();
    println!("paper session complete ({})", gateway.venue_name());
    println!(
        "  position={} realized_pnl={} fees={} equity={} killed={}",
        gateway.position(),
        m.realized_pnl(),
        m.fees_paid(),
        gateway.equity(),
        gateway.is_killed(),
    );
    Ok(())
}

fn run_replay(
    path: &Path,
    gateway: &mut PaperGateway,
    step: &mut impl FnMut(&mut PaperGateway, &MarketEvent),
) -> anyhow::Result<()> {
    let reader = EventReader::open(path).with_context(|| format!("opening {}", path.display()))?;
    info!(file = %path.display(), "replaying event log through the paper engine");
    for item in reader {
        let event = item?;
        step(gateway, &event);
    }
    Ok(())
}

fn run_live(
    config: &EngineConfig,
    duration_secs: u64,
    metrics: &Arc<TradingMetrics>,
    gateway: &mut PaperGateway,
    step: &mut impl FnMut(&mut PaperGateway, &MarketEvent),
) -> anyhow::Result<()> {
    use quantis_market_data::ws::{FeedConfig, FeedStats, run_feed};
    use tokio::sync::mpsc;

    let feed_config = FeedConfig {
        url: config.market_data.ws_url.clone(),
        coin: config.instrument.symbol.clone(),
    };
    let stats = Arc::new(FeedStats::default());
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    info!(duration_secs, "paper trading against the live feed");

    runtime.block_on(async {
        let (tx, mut rx) = mpsc::channel::<MarketEvent>(config.market_data.channel_capacity);
        let feed = tokio::spawn(run_feed(feed_config, tx, Arc::clone(&stats)));
        let deadline = tokio::time::Instant::now() + Duration::from_secs(duration_secs);
        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                break;
            }
            match tokio::time::timeout(remaining, rx.recv()).await {
                Ok(Some(event)) => {
                    step(gateway, &event);
                    metrics.set_reconnects(stats.snapshot().disconnects);
                }
                Ok(None) | Err(_) => break,
            }
        }
        feed.abort();
        anyhow::Ok(())
    })
}

/// Serve `/metrics` on `127.0.0.1:port` from a background thread. A minimal
/// HTTP/1.1 responder — all a Prometheus scrape needs, no web framework.
fn serve_metrics(metrics: Arc<TradingMetrics>, port: u16) -> anyhow::Result<()> {
    let listener = TcpListener::bind(("127.0.0.1", port))
        .with_context(|| format!("binding metrics port {port}"))?;
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let mut buf = [0u8; 1024];
            let _ = stream.read(&mut buf); // consume the request line; path ignored
            let body = metrics.render_prometheus();
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4\r\n\
                 Content-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            let _ = stream.write_all(response.as_bytes());
        }
    });
    Ok(())
}
