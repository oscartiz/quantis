//! Order execution: state machine, paper/testnet gateways, reconciliation.
//!
//! - [`order`]: order identity (idempotent client ids), lifecycle, reports.
//! - [`manager`]: the order manager — idempotent report application + position.
//! - [`gateway`]: the [`gateway::OrderGateway`] trait both gateways implement.
//! - [`paper`]: the paper gateway, which fills against the **same**
//!   `quantis_backtest` matching engine as the backtester and vets every order
//!   through the **same** `quantis_risk` gate.
//!
//! A mainnet gateway is intentionally absent; the config layer rejects
//! `mode = "mainnet"` with no bypass.

pub mod gateway;
pub mod manager;
pub mod metrics;
pub mod order;
pub mod paper;
pub mod reconcile;
pub mod testnet;

pub use gateway::{GatewayError, OrderGateway};
pub use manager::{Applied, OrderManager};
pub use metrics::TradingMetrics;
pub use order::{
    ClientOrderId, CloidGenerator, ExecReport, Order, OrderKind, OrderRequest, OrderStatus,
};
pub use paper::PaperGateway;
pub use reconcile::{ReconcileReport, reconcile};
pub use testnet::{ActionSigner, TestnetGateway};
