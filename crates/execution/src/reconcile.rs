//! Position reconciliation against an external source of truth.
//!
//! The local [`crate::OrderManager`] tracks position from the reports it has
//! seen. Reality (the exchange clearinghouse) is authoritative. Reconciliation
//! periodically compares the two and reports any drift, so a missed or
//! duplicated report surfaces as a number rather than as a silent loss. For the
//! paper venue the "exchange" position is recomputed independently, which is
//! exactly the invariant the chaos test checks: after a disruption, the tracked
//! position must still equal the truth.

use quantis_core::types::Qty;
use tracing::{info, warn};

/// Result of comparing the local position to the exchange's.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReconcileReport {
    /// Position the local manager believes it holds.
    pub local: Qty,
    /// Position the exchange reports.
    pub exchange: Qty,
    /// `exchange - local` (zero when in sync).
    pub drift: Qty,
    /// True if the drift is within tolerance.
    pub in_sync: bool,
}

/// Compare local and exchange positions; drift beyond `tolerance` is flagged.
///
/// On drift, the exchange is authoritative — the caller should adopt the
/// exchange position and investigate, never trade on the stale local view.
pub fn reconcile(local: Qty, exchange: Qty, tolerance: Qty) -> ReconcileReport {
    let drift = exchange - local;
    let in_sync = drift.abs().raw() <= tolerance.raw().abs();
    if in_sync {
        info!(local = %local, exchange = %exchange, "position reconciled");
    } else {
        warn!(
            local = %local,
            exchange = %exchange,
            drift = %drift,
            "POSITION DRIFT detected; exchange is authoritative"
        );
    }
    ReconcileReport {
        local,
        exchange,
        drift,
        in_sync,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn qty(s: &str) -> Qty {
        s.parse().unwrap()
    }

    #[test]
    fn in_sync_when_positions_match() {
        let r = reconcile(qty("0.5"), qty("0.5"), Qty::ZERO);
        assert!(r.in_sync);
        assert_eq!(r.drift, Qty::ZERO);
    }

    #[test]
    fn flags_drift_beyond_tolerance() {
        let r = reconcile(qty("0.5"), qty("0.7"), qty("0.01"));
        assert!(!r.in_sync);
        assert_eq!(r.drift, qty("0.2"));
    }

    #[test]
    fn small_drift_within_tolerance_is_ok() {
        let r = reconcile(qty("0.5"), qty("0.505"), qty("0.01"));
        assert!(r.in_sync);
    }
}
