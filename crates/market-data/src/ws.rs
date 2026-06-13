//! Resilient Hyperliquid WebSocket feed.
//!
//! Hyperliquid's l2Book feed has no sequence numbers (it is a full-snapshot
//! feed), so resilience here means: reconnect with capped exponential backoff
//! and jitter, an application-level ping inside the server's 60s idle
//! timeout, a staleness watchdog that forces a reconnect when the feed goes
//! quiet, and **bounded** delivery to the consumer — if the consumer falls
//! behind, events are dropped and counted, never silently buffered into
//! unbounded memory.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use quantis_core::events::MarketEvent;
use quantis_core::types::TsNanos;
use rand::Rng;
use tokio::sync::mpsc;
use tokio::time::{Instant, MissedTickBehavior, interval};
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, info, warn};

use crate::hl::{self, HlMessage, PING_JSON};

/// Feed configuration. All values come from the engine TOML; no defaults here.
#[derive(Debug, Clone)]
pub struct FeedConfig {
    /// WebSocket URL (e.g. `wss://api.hyperliquid.xyz/ws`).
    pub url: String,
    /// Coin to subscribe to (e.g. `"BTC"`).
    pub coin: String,
}

/// Monotonic feed counters, shared with operators/tests via [`FeedStats::snapshot`].
#[derive(Debug, Default)]
pub struct FeedStats {
    connects: AtomicU64,
    disconnects: AtomicU64,
    messages: AtomicU64,
    events: AtomicU64,
    parse_errors: AtomicU64,
    dropped: AtomicU64,
}

/// A point-in-time copy of [`FeedStats`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FeedStatsSnapshot {
    /// Successful connections established.
    pub connects: u64,
    /// Connections lost (any reason).
    pub disconnects: u64,
    /// Raw messages received.
    pub messages: u64,
    /// Normalized events emitted downstream.
    pub events: u64,
    /// Messages that failed to parse.
    pub parse_errors: u64,
    /// Events dropped because the consumer's channel was full.
    pub dropped: u64,
}

impl FeedStats {
    /// Copy the counters.
    pub fn snapshot(&self) -> FeedStatsSnapshot {
        FeedStatsSnapshot {
            connects: self.connects.load(Ordering::Relaxed),
            disconnects: self.disconnects.load(Ordering::Relaxed),
            messages: self.messages.load(Ordering::Relaxed),
            events: self.events.load(Ordering::Relaxed),
            parse_errors: self.parse_errors.load(Ordering::Relaxed),
            dropped: self.dropped.load(Ordering::Relaxed),
        }
    }
}

/// How often we proactively ping (server closes idle connections at 60s).
const PING_INTERVAL: Duration = Duration::from_secs(30);
/// Force a reconnect if nothing arrives for this long.
const STALENESS_LIMIT: Duration = Duration::from_secs(75);
/// Backoff parameters: 250ms doubling to a 30s cap, plus up-to-50% jitter.
const BACKOFF_BASE: Duration = Duration::from_millis(250);
const BACKOFF_CAP: Duration = Duration::from_secs(30);

/// Run the feed until the receiving side of `tx` is dropped.
///
/// Reconnects forever with capped backoff; transient failures are logged and
/// counted, not propagated — the operator's signal is the stats, the logs,
/// and (Phase 4) the metrics endpoint.
pub async fn run_feed(config: FeedConfig, tx: mpsc::Sender<MarketEvent>, stats: Arc<FeedStats>) {
    // rustls 0.23 requires a process-level crypto provider; select ring
    // explicitly (a second install attempt errors harmlessly).
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut attempt: u32 = 0;
    loop {
        match connect_and_stream(&config, &tx, &stats).await {
            Ok(()) => {
                info!("feed consumer closed; stopping");
                return;
            }
            Err(err) => {
                stats.disconnects.fetch_add(1, Ordering::Relaxed);
                attempt += 1;
                let delay = backoff_delay(attempt);
                warn!(error = %err, attempt, ?delay, "feed disconnected; backing off");
                tokio::time::sleep(delay).await;
            }
        }
        if tx.is_closed() {
            return;
        }
        // A connection that survived long enough to deliver data resets the
        // backoff inside connect_and_stream by returning attempt via stats;
        // simpler: reset after any successful connect is recorded.
        if stats.messages.load(Ordering::Relaxed) > 0 {
            attempt = attempt.min(5);
        }
    }
}

/// One connection lifetime. `Ok(())` means the consumer hung up (clean stop);
/// `Err` means the connection failed and the caller should reconnect.
async fn connect_and_stream(
    config: &FeedConfig,
    tx: &mpsc::Sender<MarketEvent>,
    stats: &FeedStats,
) -> Result<(), tokio_tungstenite::tungstenite::Error> {
    let (mut ws, _resp) = connect_async(&config.url).await?;
    stats.connects.fetch_add(1, Ordering::Relaxed);
    info!(url = %config.url, coin = %config.coin, "connected; subscribing");

    for channel in ["l2Book", "trades"] {
        ws.send(Message::Text(
            hl::subscribe_json(channel, &config.coin).into(),
        ))
        .await?;
    }

    let mut ping_timer = interval(PING_INTERVAL);
    ping_timer.set_missed_tick_behavior(MissedTickBehavior::Delay);
    let mut last_msg = Instant::now();

    loop {
        tokio::select! {
            _ = ping_timer.tick() => {
                if last_msg.elapsed() > STALENESS_LIMIT {
                    warn!("feed stale (no messages in {STALENESS_LIMIT:?}); reconnecting");
                    return Err(tokio_tungstenite::tungstenite::Error::ConnectionClosed);
                }
                ws.send(Message::Text(PING_JSON.into())).await?;
            }
            msg = ws.next() => {
                let Some(msg) = msg else {
                    return Err(tokio_tungstenite::tungstenite::Error::ConnectionClosed);
                };
                last_msg = Instant::now();
                match msg? {
                    Message::Text(text) => {
                        stats.messages.fetch_add(1, Ordering::Relaxed);
                        let recv_ts = TsNanos::now();
                        if !handle_text(text.as_str(), recv_ts, tx, stats) {
                            return Ok(());
                        }
                    }
                    Message::Close(frame) => {
                        debug!(?frame, "server sent close");
                        return Err(tokio_tungstenite::tungstenite::Error::ConnectionClosed);
                    }
                    // Pings are answered by tungstenite; binary is unexpected
                    // from this API and ignored by the tolerant-input policy.
                    _ => {}
                }
            }
        }
    }
}

/// Forward parsed events; returns `false` when the consumer has hung up.
fn handle_text(
    text: &str,
    recv_ts: TsNanos,
    tx: &mpsc::Sender<MarketEvent>,
    stats: &FeedStats,
) -> bool {
    let parsed = match hl::parse_message(text, recv_ts) {
        Ok(p) => p,
        Err(err) => {
            let n = stats.parse_errors.fetch_add(1, Ordering::Relaxed) + 1;
            if n <= 5 || n.is_multiple_of(1000) {
                warn!(error = %err, count = n, "failed to parse feed message");
            }
            return true;
        }
    };
    let events: Vec<MarketEvent> = match parsed {
        HlMessage::Book(snap) => vec![MarketEvent::L2Snapshot(snap)],
        HlMessage::Trades(trades) => trades.into_iter().map(MarketEvent::Trade).collect(),
        HlMessage::SubscriptionAck | HlMessage::Pong | HlMessage::Other => vec![],
    };
    for event in events {
        match tx.try_send(event) {
            Ok(()) => {
                stats.events.fetch_add(1, Ordering::Relaxed);
            }
            Err(mpsc::error::TrySendError::Full(_)) => {
                let n = stats.dropped.fetch_add(1, Ordering::Relaxed) + 1;
                if n == 1 || n.is_multiple_of(1000) {
                    warn!(
                        dropped = n,
                        "consumer behind; dropping events (bounded channel)"
                    );
                }
            }
            Err(mpsc::error::TrySendError::Closed(_)) => return false,
        }
    }
    true
}

fn backoff_delay(attempt: u32) -> Duration {
    let exp = BACKOFF_BASE.saturating_mul(2u32.saturating_pow(attempt.saturating_sub(1).min(10)));
    let capped = exp.min(BACKOFF_CAP);
    let jitter_ns = rand::rng().random_range(0..=capped.as_nanos() as u64 / 2);
    capped + Duration::from_nanos(jitter_ns)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_is_capped_with_bounded_jitter() {
        for attempt in 1..50 {
            let d = backoff_delay(attempt);
            assert!(d >= BACKOFF_BASE);
            assert!(
                d <= BACKOFF_CAP + BACKOFF_CAP / 2,
                "attempt {attempt}: {d:?}"
            );
        }
    }
}
