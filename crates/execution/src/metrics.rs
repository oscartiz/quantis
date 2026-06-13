//! Trading metrics in Prometheus text-exposition format.
//!
//! Counters and gauges are plain atomics so the trading loop can update them on
//! the hot path without locking, and a separate thread can render them for a
//! `/metrics` scrape (the HTTP serving lives in the CLI). Rendering is
//! dependency-free: the Prometheus text format is simple enough that owning the
//! ~20 lines is clearer than pulling a client library.

use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};

use quantis_core::types::{Cash, Qty};

/// Atomic trading metrics shared between the trading loop and the scrape thread.
#[derive(Debug, Default)]
pub struct TradingMetrics {
    orders_submitted: AtomicU64,
    orders_filled: AtomicU64,
    orders_rejected: AtomicU64,
    orders_cancelled: AtomicU64,
    fills_total: AtomicU64,
    reports_duplicate: AtomicU64,
    feed_reconnects: AtomicU64,
    feed_events: AtomicU64,
    // Gauges, stored as 1e8 fixed-point raw integers.
    position_raw: AtomicI64,
    equity_raw: AtomicI64,
}

impl TradingMetrics {
    /// New zeroed metrics.
    pub fn new() -> Self {
        Self::default()
    }

    /// Record a submitted order.
    pub fn on_submit(&self) {
        self.orders_submitted.fetch_add(1, Ordering::Relaxed);
    }
    /// Record a fill.
    pub fn on_fill(&self) {
        self.fills_total.fetch_add(1, Ordering::Relaxed);
    }
    /// Record an order reaching the filled state.
    pub fn on_order_filled(&self) {
        self.orders_filled.fetch_add(1, Ordering::Relaxed);
    }
    /// Record a rejection.
    pub fn on_reject(&self) {
        self.orders_rejected.fetch_add(1, Ordering::Relaxed);
    }
    /// Record a cancellation.
    pub fn on_cancel(&self) {
        self.orders_cancelled.fetch_add(1, Ordering::Relaxed);
    }
    /// Record a duplicate/ignored report (idempotency in action).
    pub fn on_duplicate(&self) {
        self.reports_duplicate.fetch_add(1, Ordering::Relaxed);
    }
    /// Record a feed reconnect.
    pub fn on_reconnect(&self) {
        self.feed_reconnects.fetch_add(1, Ordering::Relaxed);
    }
    /// Record a processed market-data event.
    pub fn on_event(&self) {
        self.feed_events.fetch_add(1, Ordering::Relaxed);
    }

    /// Set the current position gauge.
    pub fn set_position(&self, position: Qty) {
        self.position_raw.store(position.raw(), Ordering::Relaxed);
    }
    /// Set the current equity gauge.
    pub fn set_equity(&self, equity: Cash) {
        self.equity_raw.store(equity.raw(), Ordering::Relaxed);
    }
    /// Mirror the feed reconnect counter from the market-data layer.
    pub fn set_reconnects(&self, n: u64) {
        self.feed_reconnects.store(n, Ordering::Relaxed);
    }

    /// Render all metrics in Prometheus text-exposition format.
    pub fn render_prometheus(&self) -> String {
        let mut out = String::with_capacity(1024);
        let counter = |out: &mut String, name: &str, help: &str, v: u64| {
            out.push_str(&format!(
                "# HELP {name} {help}\n# TYPE {name} counter\n{name} {v}\n"
            ));
        };
        let gauge = |out: &mut String, name: &str, help: &str, v: f64| {
            out.push_str(&format!(
                "# HELP {name} {help}\n# TYPE {name} gauge\n{name} {v}\n"
            ));
        };
        let g = |a: &AtomicI64| a.load(Ordering::Relaxed) as f64 / 1e8;

        counter(
            &mut out,
            "quantis_orders_submitted_total",
            "Orders submitted",
            self.orders_submitted.load(Ordering::Relaxed),
        );
        counter(
            &mut out,
            "quantis_orders_filled_total",
            "Orders fully filled",
            self.orders_filled.load(Ordering::Relaxed),
        );
        counter(
            &mut out,
            "quantis_orders_rejected_total",
            "Orders rejected (risk or venue)",
            self.orders_rejected.load(Ordering::Relaxed),
        );
        counter(
            &mut out,
            "quantis_orders_cancelled_total",
            "Orders cancelled",
            self.orders_cancelled.load(Ordering::Relaxed),
        );
        counter(
            &mut out,
            "quantis_fills_total",
            "Individual fills",
            self.fills_total.load(Ordering::Relaxed),
        );
        counter(
            &mut out,
            "quantis_reports_duplicate_total",
            "Duplicate reports ignored (idempotency)",
            self.reports_duplicate.load(Ordering::Relaxed),
        );
        counter(
            &mut out,
            "quantis_feed_reconnects_total",
            "WebSocket reconnects",
            self.feed_reconnects.load(Ordering::Relaxed),
        );
        counter(
            &mut out,
            "quantis_feed_events_total",
            "Market-data events processed",
            self.feed_events.load(Ordering::Relaxed),
        );
        gauge(
            &mut out,
            "quantis_position_qty",
            "Net position (base units)",
            g(&self.position_raw),
        );
        gauge(
            &mut out,
            "quantis_equity_usd",
            "Mark-to-mid equity (quote)",
            g(&self.equity_raw),
        );
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn renders_valid_prometheus_text() {
        let m = TradingMetrics::new();
        m.on_submit();
        m.on_submit();
        m.on_fill();
        m.on_reject();
        m.set_position("0.01".parse().unwrap());
        m.set_equity("99997.18".parse().unwrap());

        let text = m.render_prometheus();
        assert!(text.contains("quantis_orders_submitted_total 2"));
        assert!(text.contains("quantis_fills_total 1"));
        assert!(text.contains("quantis_orders_rejected_total 1"));
        assert!(text.contains("quantis_position_qty 0.01"));
        assert!(text.contains("quantis_equity_usd 99997.18"));
        // every metric has HELP and TYPE lines
        assert_eq!(
            text.matches("# TYPE").count(),
            text.matches("# HELP").count()
        );
    }
}
