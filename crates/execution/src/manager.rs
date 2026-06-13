//! The order manager: applies execution reports idempotently and maintains the
//! authoritative local position.
//!
//! "Idempotent" here is load-bearing, not a nicety: the chaos test (kill the
//! feed mid-order, reconnect, replay) depends on a re-delivered fill being a
//! no-op. The manager dedupes fills by exchange fill id and validates every
//! status transition, so no sequence of duplicated or reordered reports can
//! produce a phantom position.

use std::collections::HashMap;

use quantis_core::types::{Cash, Px, Qty, Side};
use tracing::warn;

use crate::order::{ClientOrderId, ExecReport, Order, OrderRequest, OrderStatus};

/// Outcome of applying one report.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Applied {
    /// State advanced.
    Updated,
    /// Already seen (duplicate fill, or report for a terminal order); ignored.
    Duplicate,
    /// No such order is tracked.
    UnknownOrder,
}

/// Tracks all orders and the net position they have produced.
#[derive(Debug, Default)]
pub struct OrderManager {
    orders: HashMap<ClientOrderId, Order>,
    position: Qty,
    realized_pnl: Cash,
    fees_paid: Cash,
    // Average entry price of the current open position (for realized PnL).
    avg_entry: Px,
}

impl OrderManager {
    /// Empty manager (flat, no orders).
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a locally-submitted order. Returns `false` if the cloid is
    /// already known — resubmission is idempotent, never a second order.
    pub fn register(&mut self, request: OrderRequest) -> bool {
        if self.orders.contains_key(&request.cloid) {
            return false;
        }
        self.orders.insert(request.cloid, Order::new(request));
        true
    }

    /// Apply one execution report, idempotently.
    pub fn apply(&mut self, report: &ExecReport) -> Applied {
        let cloid = report.cloid();
        let Some(order) = self.orders.get_mut(&cloid) else {
            return Applied::UnknownOrder;
        };

        match report {
            ExecReport::Ack { .. } => {
                if order.status == OrderStatus::Pending {
                    order.status = OrderStatus::Acked;
                    Applied::Updated
                } else {
                    Applied::Duplicate
                }
            }
            ExecReport::Cancelled { .. } => {
                if order.status.is_terminal() {
                    Applied::Duplicate
                } else {
                    order.status = OrderStatus::Cancelled;
                    Applied::Updated
                }
            }
            ExecReport::Rejected { reason, .. } => {
                if order.status.is_terminal() {
                    Applied::Duplicate
                } else {
                    warn!(%cloid, reason, "order rejected");
                    order.status = OrderStatus::Rejected;
                    Applied::Updated
                }
            }
            ExecReport::Fill {
                fill_id,
                qty,
                px,
                fee,
                ..
            } => self.apply_fill(cloid, *fill_id, *qty, *px, *fee),
        }
    }

    fn apply_fill(
        &mut self,
        cloid: ClientOrderId,
        fill_id: u64,
        qty: Qty,
        px: Px,
        fee: Cash,
    ) -> Applied {
        let order = self.orders.get_mut(&cloid).expect("checked by caller");
        if order.status.is_terminal() || order.has_fill(fill_id) {
            return Applied::Duplicate;
        }
        order.record_fill(fill_id);

        // Update the order's VWAP and filled quantity.
        let prev_filled = order.filled_qty;
        let new_filled = prev_filled + qty;
        order.avg_px = vwap(order.avg_px, prev_filled, px, qty);
        order.filled_qty = new_filled;
        order.status = if order.filled_qty.raw() >= order.request.qty.raw() {
            OrderStatus::Filled
        } else {
            OrderStatus::PartiallyFilled
        };
        let side = order.request.side;

        // Update position and realized PnL.
        self.apply_to_position(side, qty, px);
        self.fees_paid += fee;
        self.realized_pnl -= fee;
        Applied::Updated
    }

    /// Update net position and realize PnL on the portion that closes.
    fn apply_to_position(&mut self, side: Side, qty: Qty, px: Px) {
        let signed = Qty::from_raw(qty.raw() * side.sign());
        let old_pos = self.position;
        let new_pos = old_pos + signed;

        let closing = old_pos.raw() != 0 && (old_pos.raw().signum() != signed.raw().signum());
        if closing {
            // Quantity that reduces the existing position realizes PnL.
            let closed_raw = old_pos.raw().abs().min(signed.raw().abs());
            let closed = Qty::from_raw(closed_raw);
            // PnL = (exit - entry) * closed_qty * sign(old position)
            let pnl_per = if old_pos.raw() > 0 {
                px - self.avg_entry
            } else {
                self.avg_entry - px
            };
            self.realized_pnl += pnl_per.notional(closed);
        }

        // New average entry: if we extended the position, blend; if we flipped,
        // the entry is the new fill price; if we only reduced, entry unchanged.
        if old_pos.raw() == 0 || old_pos.raw().signum() == signed.raw().signum() {
            // extending (or opening) -> blend entry over the same-direction size
            self.avg_entry = vwap(self.avg_entry, old_pos.abs(), px, qty);
        } else if new_pos.raw().signum() == signed.raw().signum() && new_pos.raw() != 0 {
            // flipped through flat -> new position opens at this fill price
            self.avg_entry = px;
        }
        self.position = new_pos;
        if self.position.raw() == 0 {
            self.avg_entry = Px::ZERO;
        }
    }

    /// Current net position (signed).
    pub fn position(&self) -> Qty {
        self.position
    }

    /// Average entry price of the open position (zero when flat).
    pub fn avg_entry(&self) -> Px {
        self.avg_entry
    }

    /// Realized PnL (net of fees) so far.
    pub fn realized_pnl(&self) -> Cash {
        self.realized_pnl
    }

    /// Total fees paid.
    pub fn fees_paid(&self) -> Cash {
        self.fees_paid
    }

    /// Look up a tracked order.
    pub fn order(&self, cloid: ClientOrderId) -> Option<&Order> {
        self.orders.get(&cloid)
    }

    /// Orders not yet in a terminal state.
    pub fn open_orders(&self) -> impl Iterator<Item = &Order> {
        self.orders.values().filter(|o| !o.status.is_terminal())
    }
}

/// Volume-weighted average of an existing price/size and a new price/size.
fn vwap(px_a: Px, qty_a: Qty, px_b: Px, qty_b: Qty) -> Px {
    let total = qty_a.raw() + qty_b.raw();
    if total == 0 {
        return px_b;
    }
    let num = i128::from(px_a.raw()) * i128::from(qty_a.raw())
        + i128::from(px_b.raw()) * i128::from(qty_b.raw());
    Px::from_raw((num / i128::from(total)) as i64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::order::OrderKind;

    fn px(s: &str) -> Px {
        s.parse().unwrap()
    }
    fn qty(s: &str) -> Qty {
        s.parse().unwrap()
    }
    fn req(cloid: u128, side: Side, q: &str) -> OrderRequest {
        OrderRequest {
            cloid: ClientOrderId(cloid),
            side,
            qty: qty(q),
            kind: OrderKind::Market,
            reduce_only: false,
        }
    }
    fn fill(cloid: u128, fill_id: u64, q: &str, p: &str) -> ExecReport {
        ExecReport::Fill {
            cloid: ClientOrderId(cloid),
            fill_id,
            qty: qty(q),
            px: px(p),
            fee: "0".parse().unwrap(),
        }
    }

    #[test]
    fn duplicate_fill_does_not_double_count() {
        let mut m = OrderManager::new();
        m.register(req(1, Side::Buy, "1.0"));
        assert_eq!(m.apply(&fill(1, 100, "1.0", "50000")), Applied::Updated);
        // same fill id again -> ignored
        assert_eq!(m.apply(&fill(1, 100, "1.0", "50000")), Applied::Duplicate);
        assert_eq!(m.position(), qty("1.0"));
        assert_eq!(
            m.order(ClientOrderId(1)).unwrap().status,
            OrderStatus::Filled
        );
    }

    #[test]
    fn resubmission_is_idempotent() {
        let mut m = OrderManager::new();
        assert!(m.register(req(1, Side::Buy, "1.0")));
        assert!(!m.register(req(1, Side::Buy, "1.0"))); // same cloid -> rejected
    }

    #[test]
    fn partial_fills_accumulate_then_complete() {
        let mut m = OrderManager::new();
        m.register(req(1, Side::Buy, "1.0"));
        m.apply(&fill(1, 1, "0.4", "50000"));
        assert_eq!(
            m.order(ClientOrderId(1)).unwrap().status,
            OrderStatus::PartiallyFilled
        );
        m.apply(&fill(1, 2, "0.6", "50100"));
        let o = m.order(ClientOrderId(1)).unwrap();
        assert_eq!(o.status, OrderStatus::Filled);
        assert_eq!(o.filled_qty, qty("1.0"));
        // VWAP = (0.4*50000 + 0.6*50100)/1.0 = 50060
        assert_eq!(o.avg_px, px("50060"));
    }

    #[test]
    fn realizes_pnl_on_close() {
        let mut m = OrderManager::new();
        m.register(req(1, Side::Buy, "1.0"));
        m.apply(&fill(1, 1, "1.0", "50000")); // long 1 @ 50000
        m.register(req(2, Side::Sell, "1.0"));
        m.apply(&fill(2, 2, "1.0", "51000")); // close @ 51000 -> +1000
        assert_eq!(m.position(), Qty::ZERO);
        assert_eq!(m.realized_pnl(), "1000".parse::<Cash>().unwrap());
    }

    #[test]
    fn cannot_fill_a_rejected_order() {
        let mut m = OrderManager::new();
        m.register(req(1, Side::Buy, "1.0"));
        m.apply(&ExecReport::Rejected {
            cloid: ClientOrderId(1),
            reason: "risk veto".into(),
        });
        assert_eq!(m.apply(&fill(1, 1, "1.0", "50000")), Applied::Duplicate);
        assert_eq!(m.position(), Qty::ZERO);
    }

    #[test]
    fn report_for_unknown_order_is_flagged() {
        let mut m = OrderManager::new();
        assert_eq!(m.apply(&fill(99, 1, "1.0", "50000")), Applied::UnknownOrder);
    }
}
