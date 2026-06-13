//! The gateway trait shared by the paper and testnet venues.
//!
//! Both implement the same interface so the trading loop is venue-agnostic:
//! switching from paper to testnet is a config change, not a code path. The
//! trait is deliberately small — submit, cancel, drain reports, query position
//! — and pull-based (`poll_reports`) so the caller owns the event loop.

use quantis_core::types::Qty;
use thiserror::Error;

use crate::order::{ClientOrderId, ExecReport, OrderRequest};

/// Errors a gateway can return synchronously on submit/cancel.
#[derive(Debug, Error)]
pub enum GatewayError {
    /// The risk gate vetoed the order before it left the process.
    #[error("risk gate vetoed order: {0}")]
    RiskVeto(String),
    /// The order id is already in use this session.
    #[error("duplicate client order id {0}")]
    DuplicateCloid(ClientOrderId),
    /// The venue rejected the request (network/auth/format).
    #[error("venue error: {0}")]
    Venue(String),
    /// Live trading attempted without the explicit, documented opt-in.
    #[error("live trading is gated: {0}")]
    Gated(String),
}

/// A venue that accepts orders and emits execution reports.
pub trait OrderGateway {
    /// Submit an order. Idempotent in the client id: a repeat submit of a known
    /// cloid must not create a second order.
    fn submit(&mut self, request: OrderRequest) -> Result<(), GatewayError>;

    /// Request cancellation of a working order.
    fn cancel(&mut self, cloid: ClientOrderId) -> Result<(), GatewayError>;

    /// Drain any execution reports produced since the last call.
    fn poll_reports(&mut self) -> Vec<ExecReport>;

    /// The gateway's view of the current net position.
    fn position(&self) -> Qty;

    /// A human label for logs/metrics (e.g. "paper", "testnet").
    fn venue_name(&self) -> &'static str;
}
