//! Property tests for the risk gate's safety invariants. These assert the
//! properties that must hold for *every* order, not just the hand-picked cases
//! in the unit tests: the gate is the last line of defence, so "it never lets
//! a breaching order through" must be true universally, not anecdotally.

use proptest::prelude::*;
use quantis_core::types::{Cash, Px, Qty, Side};
use quantis_risk::{RiskDecision, RiskGate, RiskLimits};

const MAX_POS: i64 = 100_000_000; // 1.0 BTC
const MAX_NOTIONAL: i64 = 150_000 * 100_000_000; // $150k

fn limits() -> RiskLimits {
    RiskLimits {
        max_position_qty: Qty::from_raw(MAX_POS),
        max_notional: Cash::from_raw(MAX_NOTIONAL),
        max_drawdown_frac: 0.20,
    }
}

fn fresh_gate() -> RiskGate {
    RiskGate::new(limits(), Cash::from_raw(100_000 * 100_000_000)).unwrap()
}

proptest! {
    /// The central safety property: if the gate ALLOWS a risk-increasing order,
    /// the resulting position is within BOTH the position and notional caps.
    /// Equivalently: the gate never lets a breaching order increase risk.
    #[test]
    fn allow_of_increasing_order_implies_within_caps(
        cur in -250_000_000i64..=250_000_000,           // -2.5 .. 2.5 BTC
        ord in 1i64..=250_000_000,                       // (0, 2.5] BTC
        buy in any::<bool>(),
        price_raw in (10_000i64 * 100_000_000)..=(500_000i64 * 100_000_000), // $10k..$500k
    ) {
        let gate = fresh_gate();
        let cur_q = Qty::from_raw(cur);
        let side = if buy { Side::Buy } else { Side::Sell };
        let price = Px::from_raw(price_raw);
        let decision = gate.check_order(cur_q, side, Qty::from_raw(ord), price);

        let delta = Qty::from_raw(ord * side.sign());
        let resulting = cur_q + delta;
        let increasing = resulting.abs().raw() > cur_q.abs().raw();

        if decision.is_allowed() && increasing {
            prop_assert!(resulting.abs().raw() <= MAX_POS,
                "allowed but position {} > cap {}", resulting.abs().raw(), MAX_POS);
            prop_assert!(price.notional(resulting).abs().raw() <= MAX_NOTIONAL,
                "allowed but notional exceeds cap");
        }
    }

    /// A de-risking order (shrinks the absolute position without flipping past
    /// flat into a larger opposite) is ALWAYS allowed — even with the kill
    /// switch tripped, you can always flatten.
    #[test]
    fn reducing_order_is_always_allowed(
        cur in -250_000_000i64..=250_000_000,
        ord in 1i64..=250_000_000,
        buy in any::<bool>(),
        tripped in any::<bool>(),
        price_raw in (10_000i64 * 100_000_000)..=(500_000i64 * 100_000_000),
    ) {
        let mut gate = fresh_gate();
        if tripped {
            gate.on_equity(Cash::from_raw(10_000 * 100_000_000)); // 90% drawdown
            prop_assert!(gate.is_killed());
        }
        let cur_q = Qty::from_raw(cur);
        let side = if buy { Side::Buy } else { Side::Sell };
        let resulting = cur_q + Qty::from_raw(ord * side.sign());
        let increasing = resulting.abs().raw() > cur_q.abs().raw();

        if !increasing {
            let decision = gate.check_order(cur_q, side, Qty::from_raw(ord), Px::from_raw(price_raw));
            prop_assert_eq!(decision, RiskDecision::Allow);
        }
    }

    /// Once the kill switch is tripped, no risk-increasing order is allowed.
    #[test]
    fn killed_gate_blocks_all_increasing_orders(
        cur in -250_000_000i64..=250_000_000,
        ord in 1i64..=250_000_000,
        buy in any::<bool>(),
        price_raw in (10_000i64 * 100_000_000)..=(500_000i64 * 100_000_000),
    ) {
        let mut gate = fresh_gate();
        gate.on_equity(Cash::from_raw(1)); // catastrophic drawdown -> kill
        prop_assert!(gate.is_killed());

        let cur_q = Qty::from_raw(cur);
        let side = if buy { Side::Buy } else { Side::Sell };
        let resulting = cur_q + Qty::from_raw(ord * side.sign());
        if resulting.abs().raw() > cur_q.abs().raw() {
            let decision = gate.check_order(cur_q, side, Qty::from_raw(ord), Px::from_raw(price_raw));
            prop_assert!(!decision.is_allowed(), "killed gate allowed an increasing order");
        }
    }
}
