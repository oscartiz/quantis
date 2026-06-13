//! Chaos test: kill the feed mid-order, reconnect, and replay — and prove the
//! tracked position is still correct, with no phantom fills.
//!
//! This is the integration-level statement of the idempotency the order
//! manager promises. A real reconnect re-subscribes and the exchange may
//! re-deliver recent fills (and our own resend logic may resubmit an order);
//! a naive system double-counts and ends up holding a position it never took.
//! Here we simulate exactly that and assert the position matches an
//! independently computed ground truth.

use quantis_core::types::{Cash, Px, Qty, Side};
use quantis_execution::order::OrderKind;
use quantis_execution::{
    Applied, ClientOrderId, ExecReport, OrderManager, OrderRequest, ReconcileReport, reconcile,
};

fn px(s: &str) -> Px {
    s.parse().unwrap()
}
fn qty(s: &str) -> Qty {
    s.parse().unwrap()
}
fn market(cloid: u128, side: Side, q: &str) -> OrderRequest {
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
        fee: Cash::ZERO,
    }
}

#[test]
fn feed_kill_and_replay_produces_no_phantom_position() {
    let mut m = OrderManager::new();

    // --- before the incident: two orders, one filled, one filling ---
    m.register(market(1, Side::Buy, "0.30"));
    m.register(market(2, Side::Buy, "0.20"));
    assert_eq!(m.apply(&fill(1, 1001, "0.30", "50000")), Applied::Updated);
    assert_eq!(m.apply(&fill(2, 1002, "0.20", "50010")), Applied::Updated);
    let position_before = m.position();
    assert_eq!(position_before, qty("0.50"));

    // --- the incident: feed dies mid-stream, then reconnects ---
    // On reconnect the exchange RE-DELIVERS the recent fills (a real behavior),
    // and our resend logic RE-SUBMITS the in-flight orders (same cloids).
    let resubmit_1 = m.register(market(1, Side::Buy, "0.30")); // duplicate cloid
    let resubmit_2 = m.register(market(2, Side::Buy, "0.20"));
    assert!(
        !resubmit_1 && !resubmit_2,
        "resends must not create new orders"
    );

    // re-delivered fills with the SAME fill ids must be ignored
    assert_eq!(m.apply(&fill(1, 1001, "0.30", "50000")), Applied::Duplicate);
    assert_eq!(m.apply(&fill(2, 1002, "0.20", "50010")), Applied::Duplicate);
    // reordered re-delivery too
    assert_eq!(m.apply(&fill(2, 1002, "0.20", "50010")), Applied::Duplicate);

    // --- after recovery: position is unchanged, no phantom exposure ---
    assert_eq!(
        m.position(),
        position_before,
        "phantom position after replay"
    );

    // a genuinely new fill (post-reconnect) still applies exactly once
    m.register(market(3, Side::Sell, "0.50"));
    assert_eq!(m.apply(&fill(3, 1003, "0.50", "50100")), Applied::Updated);
    assert_eq!(m.apply(&fill(3, 1003, "0.50", "50100")), Applied::Duplicate);
    assert_eq!(m.position(), Qty::ZERO);

    // --- reconcile against exchange ground truth: in sync ---
    let exchange_truth = Qty::ZERO; // exchange agrees we are flat
    let report: ReconcileReport = reconcile(m.position(), exchange_truth, Qty::ZERO);
    assert!(report.in_sync, "local and exchange disagree: {report:?}");
    assert_eq!(report.drift, Qty::ZERO);
}

#[test]
fn detects_drift_when_a_fill_is_genuinely_missed() {
    // If a fill is truly lost (not just duplicated), reconciliation must catch
    // the gap rather than let the system trade on a wrong position.
    let mut m = OrderManager::new();
    m.register(market(1, Side::Buy, "1.0"));
    m.apply(&fill(1, 1, "1.0", "50000"));

    // exchange actually filled an extra 0.2 we never heard about
    let exchange_truth = qty("1.2");
    let report = reconcile(m.position(), exchange_truth, qty("0.01"));
    assert!(!report.in_sync);
    assert_eq!(report.drift, qty("0.2"));
}
