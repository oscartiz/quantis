//! Order identity, lifecycle, and execution reports.
//!
//! The lifecycle is a small state machine with one safety-critical property:
//! **applying the same execution report twice must not change state twice.**
//! Networks duplicate and reorder; reconnects replay. An order manager that
//! double-counts a re-delivered fill invents a phantom position. So fills are
//! deduplicated by exchange fill id, and every transition is validated against
//! the current status rather than blindly applied.

use std::fmt;

use quantis_core::types::{Px, Qty, Side};

/// A 128-bit client order id (Hyperliquid `cloid`): an idempotency key chosen
/// by us so a resend cannot create a second order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ClientOrderId(pub u128);

impl ClientOrderId {
    /// Render as the `0x`-prefixed 32-hex-digit string the exchange expects.
    pub fn to_hex(self) -> String {
        format!("0x{:032x}", self.0)
    }
}

impl fmt::Display for ClientOrderId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_hex())
    }
}

/// Deterministic, collision-resistant client order ids from a per-session seed
/// and a monotonic counter. Deterministic ids make runs reproducible and make
/// a resubmission reuse the *same* id (idempotency) rather than mint a new one.
#[derive(Debug, Clone)]
pub struct CloidGenerator {
    session: u64,
    counter: u64,
}

impl CloidGenerator {
    /// New generator for a session (e.g. derived from the engine seed).
    pub fn new(session: u64) -> Self {
        Self {
            session,
            counter: 0,
        }
    }

    /// Next id: high 64 bits are the session, low 64 the counter.
    pub fn next_id(&mut self) -> ClientOrderId {
        let id = (u128::from(self.session) << 64) | u128::from(self.counter);
        self.counter += 1;
        ClientOrderId(id)
    }
}

/// Order type. Phase 4 ships market orders; limit is wired for the maker path.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderKind {
    /// Cross the spread immediately.
    Market,
    /// Rest at a price (reduce-only handling and queue model land with the
    /// maker strategy, ADR-004).
    Limit(Px),
}

/// A request to the gateway. `reduce_only` lets de-risking orders bypass the
/// risk gate's increasing-exposure checks (you can always flatten).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OrderRequest {
    /// Idempotency key.
    pub cloid: ClientOrderId,
    /// Buy or sell.
    pub side: Side,
    /// Quantity (positive).
    pub qty: Qty,
    /// Market or limit.
    pub kind: OrderKind,
    /// Whether this order may only reduce the position.
    pub reduce_only: bool,
}

/// Where an order is in its lifecycle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderStatus {
    /// Submitted locally, not yet acknowledged by the venue.
    Pending,
    /// Acknowledged (resting or working).
    Acked,
    /// Some quantity filled, more outstanding.
    PartiallyFilled,
    /// Fully filled (terminal).
    Filled,
    /// Cancelled (terminal).
    Cancelled,
    /// Rejected by the venue or risk gate (terminal).
    Rejected,
}

impl OrderStatus {
    /// True if no further transitions are possible.
    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Filled | Self::Cancelled | Self::Rejected)
    }
}

/// An execution report from a gateway (ack, fill, cancel, reject).
#[derive(Debug, Clone, PartialEq)]
pub enum ExecReport {
    /// The venue acknowledged the order.
    Ack {
        /// Which order.
        cloid: ClientOrderId,
    },
    /// A (possibly partial) fill.
    Fill {
        /// Which order.
        cloid: ClientOrderId,
        /// Exchange-unique fill id, used for deduplication.
        fill_id: u64,
        /// Filled quantity (positive).
        qty: Qty,
        /// Fill price.
        px: Px,
        /// Fee charged (positive).
        fee: quantis_core::types::Cash,
    },
    /// The order was cancelled.
    Cancelled {
        /// Which order.
        cloid: ClientOrderId,
    },
    /// The order was rejected.
    Rejected {
        /// Which order.
        cloid: ClientOrderId,
        /// Human-readable reason.
        reason: String,
    },
}

impl ExecReport {
    /// The order this report concerns.
    pub fn cloid(&self) -> ClientOrderId {
        match self {
            Self::Ack { cloid }
            | Self::Fill { cloid, .. }
            | Self::Cancelled { cloid }
            | Self::Rejected { cloid, .. } => *cloid,
        }
    }
}

/// A tracked order and its accumulated fills.
#[derive(Debug, Clone)]
pub struct Order {
    /// Original request.
    pub request: OrderRequest,
    /// Current status.
    pub status: OrderStatus,
    /// Cumulative filled quantity.
    pub filled_qty: Qty,
    /// Volume-weighted average fill price (zero until first fill).
    pub avg_px: Px,
    /// Fill ids already applied (dedup guard).
    seen_fills: Vec<u64>,
}

impl Order {
    /// A freshly submitted order in `Pending`.
    pub fn new(request: OrderRequest) -> Self {
        Self {
            request,
            status: OrderStatus::Pending,
            filled_qty: Qty::ZERO,
            avg_px: Px::ZERO,
            seen_fills: Vec::new(),
        }
    }

    /// Remaining unfilled quantity.
    pub fn remaining(&self) -> Qty {
        self.request.qty - self.filled_qty
    }

    /// Whether a fill id has already been applied (dedup guard).
    pub fn has_fill(&self, fill_id: u64) -> bool {
        self.seen_fills.contains(&fill_id)
    }

    /// Record that a fill id has been applied.
    pub fn record_fill(&mut self, fill_id: u64) {
        self.seen_fills.push(fill_id);
    }
}
