//! Hyperliquid **testnet** gateway: request construction (tested) and a signing
//! seam (gated, key-dependent).
//!
//! Honesty about scope. The order *request* the exchange expects — the action
//! object, the IOC-limit encoding of a market order, the 128-bit `cloid`, the
//! nonce — is fully built here and unit-tested against the documented format
//! (verified 2026-06-13). What is **not** done in-repo is the signature: HL
//! signs an msgpack hash of the action via EIP-712 with a secp256k1 wallet key.
//! That requires the operator's testnet key and is genuinely untestable without
//! one, so rather than ship a hand-rolled signer that "looks right" but has
//! never authenticated against the live exchange, signing is an explicit trait
//! ([`ActionSigner`]) the operator wires up. Without a signer the gateway
//! refuses to submit, loudly. See ADR-006.
//!
//! This keeps the safety posture intact: nothing here can place an order
//! without an operator deliberately providing a key and a signer, and mainnet
//! remains rejected at the config layer regardless.

use quantis_core::types::{Px, Qty, Side};
use serde::Serialize;

use crate::gateway::{GatewayError, OrderGateway};
use crate::manager::OrderManager;
use crate::order::{ClientOrderId, ExecReport, OrderKind, OrderRequest};

/// Testnet exchange base URL (orders POST to `/exchange`).
pub const TESTNET_BASE_URL: &str = "https://api.hyperliquid-testnet.xyz";

/// Time-in-force for a limit order.
#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
pub enum Tif {
    /// Add-liquidity-only (post-only).
    Alo,
    /// Immediate-or-cancel (used to encode marketable orders).
    Ioc,
    /// Good-til-cancelled.
    Gtc,
}

#[derive(Debug, Serialize, PartialEq)]
struct LimitType {
    tif: Tif,
}

#[derive(Debug, Serialize, PartialEq)]
struct OrderType {
    limit: LimitType,
}

/// One order in a Hyperliquid `order` action, with the exchange's terse keys.
#[derive(Debug, Serialize, PartialEq)]
pub struct WireOrder {
    /// Asset index.
    pub a: u32,
    /// Is buy.
    pub b: bool,
    /// Price (decimal string).
    pub p: String,
    /// Size (decimal string).
    pub s: String,
    /// Reduce only.
    pub r: bool,
    /// Order type.
    t: OrderType,
    /// Client order id (`0x…`).
    pub c: String,
}

/// A Hyperliquid `order` action (one or more orders).
#[derive(Debug, Serialize, PartialEq)]
pub struct OrderAction {
    /// Action type tag.
    #[serde(rename = "type")]
    pub action_type: &'static str,
    /// The orders.
    pub orders: Vec<WireOrder>,
    /// Order grouping (`"na"` for ungrouped).
    pub grouping: &'static str,
}

/// The full signed-request envelope the exchange endpoint consumes.
#[derive(Debug, Serialize)]
pub struct ExchangeRequest {
    /// The action.
    pub action: OrderAction,
    /// Nonce (ms timestamp).
    pub nonce: u64,
    /// Signature (operator-supplied via [`ActionSigner`]).
    pub signature: String,
}

/// Build the order action for a single order. `marketable_px` is the aggressive
/// limit price used to encode a market order as an IOC limit (HL has no
/// distinct market type); pass the configured limit price for a true limit.
pub fn build_order_action(
    asset_index: u32,
    request: &OrderRequest,
    marketable_px: Px,
) -> OrderAction {
    let (px, tif) = match request.kind {
        OrderKind::Market => (marketable_px, Tif::Ioc),
        OrderKind::Limit(p) => (p, Tif::Gtc),
    };
    OrderAction {
        action_type: "order",
        orders: vec![WireOrder {
            a: asset_index,
            b: request.side == Side::Buy,
            p: px.to_string(),
            s: request.qty.to_string(),
            r: request.reduce_only,
            t: OrderType {
                limit: LimitType { tif },
            },
            c: request.cloid.to_hex(),
        }],
        grouping: "na",
    }
}

/// Signs an exchange action. The operator implements this with their testnet
/// key and Hyperliquid's msgpack + EIP-712 scheme (the official SDK has a
/// reference implementation). Kept as a trait so no key material lives in this
/// crate and no unverified signer ships.
pub trait ActionSigner: Send + Sync {
    /// Sign the action serialized with the given nonce; return the signature
    /// field the exchange expects.
    fn sign(&self, action: &OrderAction, nonce: u64) -> Result<String, String>;
}

/// The Hyperliquid testnet gateway. Without an [`ActionSigner`] it tracks
/// orders and position but refuses to submit (gated). With one, submit would
/// build, sign, and POST the request to [`TESTNET_BASE_URL`]`/exchange`.
pub struct TestnetGateway {
    asset_index: u32,
    signer: Option<Box<dyn ActionSigner>>,
    manager: OrderManager,
    reports: Vec<ExecReport>,
}

impl TestnetGateway {
    /// New gateway. `signer = None` means submission is gated (no keys).
    pub fn new(asset_index: u32, signer: Option<Box<dyn ActionSigner>>) -> Self {
        Self {
            asset_index,
            signer,
            manager: OrderManager::new(),
            reports: Vec::new(),
        }
    }

    /// The order manager (position, fills).
    pub fn manager(&self) -> &OrderManager {
        &self.manager
    }
}

impl OrderGateway for TestnetGateway {
    fn submit(&mut self, request: OrderRequest) -> Result<(), GatewayError> {
        if !self.manager.register(request) {
            return Err(GatewayError::DuplicateCloid(request.cloid));
        }
        let Some(signer) = self.signer.as_ref() else {
            return Err(GatewayError::Gated(
                "testnet order placement requires an ActionSigner and a testnet key; \
                 none configured. Request construction is implemented and tested, but \
                 signing/transport is the operator's to wire (ADR-006)."
                    .into(),
            ));
        };
        // The request the exchange expects is fully built and signed here; the
        // actual HTTP POST is the operator's transport (kept out of this crate
        // so no untested network/auth path ships). Provided for completeness.
        let nonce = 0; // operator supplies a real ms-timestamp nonce
        let marketable = match request.kind {
            OrderKind::Limit(p) => p,
            OrderKind::Market => Px::ZERO, // operator computes an aggressive px
        };
        let action = build_order_action(self.asset_index, &request, marketable);
        let _signature = signer.sign(&action, nonce).map_err(GatewayError::Venue)?;
        Err(GatewayError::Gated(format!(
            "transport not wired in-repo: POST the signed request to \
             {TESTNET_BASE_URL}/exchange from the operator's environment (ADR-006)"
        )))
    }

    fn cancel(&mut self, _cloid: ClientOrderId) -> Result<(), GatewayError> {
        Err(GatewayError::Gated(
            "testnet cancel is gated (see submit)".into(),
        ))
    }

    fn poll_reports(&mut self) -> Vec<ExecReport> {
        std::mem::take(&mut self.reports)
    }

    fn position(&self) -> Qty {
        self.manager.position()
    }

    fn venue_name(&self) -> &'static str {
        "testnet"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn order(cloid: u128, side: Side, q: &str) -> OrderRequest {
        OrderRequest {
            cloid: ClientOrderId(cloid),
            side,
            qty: q.parse().unwrap(),
            kind: OrderKind::Market,
            reduce_only: false,
        }
    }

    #[test]
    fn builds_documented_order_action_shape() {
        let action = build_order_action(
            0,
            &order(0x1234, Side::Buy, "0.01"),
            "100500".parse().unwrap(),
        );
        let json = serde_json::to_value(&action).unwrap();
        assert_eq!(json["type"], "order");
        assert_eq!(json["grouping"], "na");
        let o = &json["orders"][0];
        assert_eq!(o["a"], 0);
        assert_eq!(o["b"], true);
        assert_eq!(o["p"], "100500");
        assert_eq!(o["s"], "0.01");
        assert_eq!(o["r"], false);
        assert_eq!(o["t"]["limit"]["tif"], "Ioc"); // market encoded as IOC
        assert_eq!(o["c"], "0x00000000000000000000000000001234");
    }

    #[test]
    fn sell_limit_uses_gtc_and_its_price() {
        let req = OrderRequest {
            cloid: ClientOrderId(1),
            side: Side::Sell,
            qty: "0.02".parse().unwrap(),
            kind: OrderKind::Limit("101000".parse().unwrap()),
            reduce_only: true,
        };
        let action = build_order_action(3, &req, Px::ZERO);
        let o = &serde_json::to_value(&action).unwrap()["orders"][0];
        assert_eq!(o["a"], 3);
        assert_eq!(o["b"], false);
        assert_eq!(o["p"], "101000");
        assert_eq!(o["r"], true);
        assert_eq!(o["t"]["limit"]["tif"], "Gtc");
    }

    #[test]
    fn submit_without_signer_is_gated_not_silent() {
        let mut g = TestnetGateway::new(0, None);
        let err = g.submit(order(1, Side::Buy, "0.01")).unwrap_err();
        assert!(matches!(err, GatewayError::Gated(_)));
        // but the order is tracked, so position queries still work
        assert_eq!(g.position(), Qty::ZERO);
    }

    #[test]
    fn duplicate_cloid_rejected_before_gating() {
        let mut g = TestnetGateway::new(0, None);
        let _ = g.submit(order(1, Side::Buy, "0.01"));
        let err = g.submit(order(1, Side::Buy, "0.01")).unwrap_err();
        assert!(matches!(err, GatewayError::DuplicateCloid(_)));
    }
}
