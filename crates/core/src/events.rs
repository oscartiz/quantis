//! The normalized internal event model.
//!
//! Everything downstream — recorder, order book, backtester, paper trader —
//! consumes [`MarketEvent`]s, so a strategy cannot tell (and must not care)
//! whether events came from a live socket or a recorded file.
//!
//! Events deliberately do **not** carry an instrument field: an event log is
//! single-instrument by design and names its instrument once in the log
//! header (see `quantis-market-data`). Multi-instrument trading composes
//! multiple streams; that keeps the hot path free of per-event strings.

use serde::{Deserialize, Serialize};

use crate::types::{Px, Qty, Side, TsNanos};

/// A normalized market data event.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum MarketEvent {
    /// A trade print.
    Trade(Trade),
    /// A full order-book snapshot (Hyperliquid's l2Book feed pushes
    /// snapshots, throttled to at most one per 0.5s block interval).
    L2Snapshot(L2Snapshot),
    /// A completed candle.
    Candle(Candle),
}

impl MarketEvent {
    /// The exchange-assigned timestamp of the event.
    pub fn exch_ts(&self) -> TsNanos {
        match self {
            MarketEvent::Trade(t) => t.exch_ts,
            MarketEvent::L2Snapshot(s) => s.exch_ts,
            MarketEvent::Candle(c) => c.close_ts,
        }
    }

    /// When this process received the event (includes clock skew vs.
    /// `exch_ts`; latency reports must say so).
    pub fn recv_ts(&self) -> TsNanos {
        match self {
            MarketEvent::Trade(t) => t.recv_ts,
            MarketEvent::L2Snapshot(s) => s.recv_ts,
            MarketEvent::Candle(c) => c.recv_ts,
        }
    }
}

/// A trade print.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Trade {
    /// Trade price.
    pub px: Px,
    /// Trade size (always positive; direction is `side`).
    pub qty: Qty,
    /// Aggressor side: `Buy` means the buyer crossed the spread.
    pub side: Side,
    /// Exchange timestamp.
    pub exch_ts: TsNanos,
    /// Local receive timestamp.
    pub recv_ts: TsNanos,
    /// Exchange trade id.
    pub tid: u64,
}

/// One price level of an order book.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Level {
    /// Level price.
    pub px: Px,
    /// Total resting size at this price.
    pub qty: Qty,
    /// Number of resting orders at this price.
    pub n_orders: u32,
}

/// A full order-book snapshot.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct L2Snapshot {
    /// Exchange timestamp.
    pub exch_ts: TsNanos,
    /// Local receive timestamp.
    pub recv_ts: TsNanos,
    /// Bid levels, best (highest price) first.
    pub bids: Vec<Level>,
    /// Ask levels, best (lowest price) first.
    pub asks: Vec<Level>,
}

/// A completed candle.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Candle {
    /// Open time (exchange).
    pub open_ts: TsNanos,
    /// Close time (exchange).
    pub close_ts: TsNanos,
    /// Local receive timestamp.
    pub recv_ts: TsNanos,
    /// Open price.
    pub open: Px,
    /// High price.
    pub high: Px,
    /// Low price.
    pub low: Px,
    /// Close price.
    pub close: Px,
    /// Base-unit volume.
    pub volume: Qty,
    /// Number of trades in the candle.
    pub n_trades: u32,
}
