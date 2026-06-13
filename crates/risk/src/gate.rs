//! The pre-trade risk gate: the authority that can veto any order.
//!
//! The gate is intentionally *integer and simple*. Sizing may use floating
//! point and clever estimates; the gate does not. It enforces a handful of
//! hard bounds that must hold no matter what the strategy or sizing intended:
//! position cap, notional cap, a portfolio drawdown limit, and a kill switch.
//! A reducing (de-risking) order is always allowed to pass — you can always
//! flatten — but a risk-*increasing* order must clear every check.

use quantis_core::types::{Cash, Px, Qty, Side};
use thiserror::Error;

/// Hard risk limits. All from config; none are defaulted silently.
#[derive(Debug, Clone, Copy)]
pub struct RiskLimits {
    /// Maximum absolute position in base units.
    pub max_position_qty: Qty,
    /// Maximum absolute position notional in quote currency.
    pub max_notional: Cash,
    /// Portfolio drawdown fraction (0..1) that trips the kill switch.
    pub max_drawdown_frac: f64,
}

impl RiskLimits {
    /// Validate the limits are sane.
    pub fn validate(&self) -> Result<(), RiskConfigError> {
        if self.max_position_qty.raw() <= 0 {
            return Err(RiskConfigError("max_position_qty must be positive"));
        }
        if self.max_notional.raw() <= 0 {
            return Err(RiskConfigError("max_notional must be positive"));
        }
        if !(0.0..=1.0).contains(&self.max_drawdown_frac) || self.max_drawdown_frac == 0.0 {
            return Err(RiskConfigError("max_drawdown_frac must be in (0, 1]"));
        }
        Ok(())
    }
}

/// Invalid risk configuration.
#[derive(Debug, Error, PartialEq, Eq)]
#[error("invalid risk limits: {0}")]
pub struct RiskConfigError(&'static str);

/// The gate's verdict on a proposed order.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RiskDecision {
    /// The order may proceed.
    Allow,
    /// The order is rejected, with a reason.
    Veto(RiskVeto),
}

impl RiskDecision {
    /// True if the order may proceed.
    pub fn is_allowed(&self) -> bool {
        matches!(self, RiskDecision::Allow)
    }
}

/// Why an order was vetoed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RiskVeto {
    /// The kill switch is tripped; only de-risking orders pass.
    KillSwitchTripped,
    /// The resulting position would exceed the position cap.
    MaxPositionExceeded {
        /// Absolute resulting position.
        resulting: Qty,
        /// The cap.
        limit: Qty,
    },
    /// The resulting position notional would exceed the notional cap.
    MaxNotionalExceeded {
        /// Absolute resulting notional.
        resulting: Cash,
        /// The cap.
        limit: Cash,
    },
}

/// Tracks portfolio state and adjudicates orders.
#[derive(Debug, Clone)]
pub struct RiskGate {
    limits: RiskLimits,
    peak_equity: Cash,
    last_equity: Cash,
    kill_switched: bool,
}

impl RiskGate {
    /// Create a gate seeded with the starting equity.
    pub fn new(limits: RiskLimits, initial_equity: Cash) -> Result<Self, RiskConfigError> {
        limits.validate()?;
        Ok(Self {
            limits,
            peak_equity: initial_equity,
            last_equity: initial_equity,
            kill_switched: false,
        })
    }

    /// Update the running equity mark. Trips (and latches) the kill switch if
    /// drawdown from the peak reaches the configured limit. The kill switch
    /// never un-trips automatically — recovery is an explicit operator action
    /// ([`RiskGate::reset_kill_switch`]).
    pub fn on_equity(&mut self, equity: Cash) {
        self.last_equity = equity;
        if equity > self.peak_equity {
            self.peak_equity = equity;
        }
        if self.peak_equity.raw() > 0 {
            let drawdown = 1.0 - (equity.to_f64() / self.peak_equity.to_f64());
            if drawdown >= self.limits.max_drawdown_frac {
                self.kill_switched = true;
            }
        }
    }

    /// Adjudicate a proposed market order.
    ///
    /// `current_position` is signed (long positive). `side`/`qty` describe the
    /// order. A reducing order (one that shrinks the absolute position without
    /// flipping past flat) always passes; a risk-increasing order must clear
    /// the kill switch and the position and notional caps.
    pub fn check_order(
        &self,
        current_position: Qty,
        side: Side,
        qty: Qty,
        price: Px,
    ) -> RiskDecision {
        let delta = Qty::from_raw(qty.raw().abs() * side.sign());
        let resulting = current_position + delta;
        let increasing = resulting.abs().raw() > current_position.abs().raw();

        // De-risking is always permitted (you can always flatten).
        if !increasing {
            return RiskDecision::Allow;
        }

        if self.kill_switched {
            return RiskDecision::Veto(RiskVeto::KillSwitchTripped);
        }
        if resulting.abs().raw() > self.limits.max_position_qty.raw() {
            return RiskDecision::Veto(RiskVeto::MaxPositionExceeded {
                resulting: resulting.abs(),
                limit: self.limits.max_position_qty,
            });
        }
        let resulting_notional = price.notional(resulting).abs();
        if resulting_notional.raw() > self.limits.max_notional.raw() {
            return RiskDecision::Veto(RiskVeto::MaxNotionalExceeded {
                resulting: resulting_notional,
                limit: self.limits.max_notional,
            });
        }
        RiskDecision::Allow
    }

    /// Whether the kill switch is currently tripped.
    pub fn is_killed(&self) -> bool {
        self.kill_switched
    }

    /// Current drawdown fraction from the peak (0 if at/above peak).
    pub fn drawdown(&self) -> f64 {
        if self.peak_equity.raw() <= 0 {
            return 0.0;
        }
        (1.0 - self.last_equity.to_f64() / self.peak_equity.to_f64()).max(0.0)
    }

    /// Explicitly clear the kill switch (operator action after investigation).
    pub fn reset_kill_switch(&mut self) {
        self.kill_switched = false;
        self.peak_equity = self.last_equity;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cash(s: &str) -> Cash {
        s.parse().unwrap()
    }
    fn px(s: &str) -> Px {
        s.parse().unwrap()
    }
    fn qty(s: &str) -> Qty {
        s.parse().unwrap()
    }

    fn limits() -> RiskLimits {
        RiskLimits {
            max_position_qty: qty("1.0"),
            max_notional: cash("150000"),
            max_drawdown_frac: 0.20,
        }
    }

    fn gate() -> RiskGate {
        RiskGate::new(limits(), cash("100000")).unwrap()
    }

    #[test]
    fn allows_orders_within_limits() {
        let d = gate().check_order(Qty::ZERO, Side::Buy, qty("0.5"), px("100000"));
        assert!(d.is_allowed());
    }

    #[test]
    fn vetoes_position_cap_breach() {
        let d = gate().check_order(qty("0.8"), Side::Buy, qty("0.5"), px("100000"));
        assert!(matches!(
            d,
            RiskDecision::Veto(RiskVeto::MaxPositionExceeded { .. })
        ));
    }

    #[test]
    fn vetoes_notional_cap_breach() {
        // 1.0 BTC at 200000 = 200000 notional > 150000 limit (position cap ok at 1.0)
        let d = gate().check_order(Qty::ZERO, Side::Buy, qty("1.0"), px("200000"));
        assert!(matches!(
            d,
            RiskDecision::Veto(RiskVeto::MaxNotionalExceeded { .. })
        ));
    }

    #[test]
    fn drawdown_trips_and_latches_kill_switch() {
        let mut g = gate();
        g.on_equity(cash("100000"));
        g.on_equity(cash("79000")); // 21% drawdown > 20%
        assert!(g.is_killed());
        // increasing order vetoed
        let d = g.check_order(Qty::ZERO, Side::Buy, qty("0.1"), px("100000"));
        assert_eq!(d, RiskDecision::Veto(RiskVeto::KillSwitchTripped));
        // recovery of equity does NOT auto-untrip
        g.on_equity(cash("100000"));
        assert!(g.is_killed());
    }

    #[test]
    fn de_risking_order_passes_even_when_killed() {
        let mut g = gate();
        g.on_equity(cash("70000")); // trip
        assert!(g.is_killed());
        // currently long 0.5, selling 0.3 reduces the position -> allowed
        let d = g.check_order(qty("0.5"), Side::Sell, qty("0.3"), px("100000"));
        assert!(d.is_allowed());
    }

    #[test]
    fn reset_clears_kill_switch() {
        let mut g = gate();
        g.on_equity(cash("70000"));
        assert!(g.is_killed());
        g.reset_kill_switch();
        assert!(!g.is_killed());
    }

    #[test]
    fn invalid_limits_rejected() {
        let bad = RiskLimits {
            max_drawdown_frac: 1.5,
            ..limits()
        };
        assert!(RiskGate::new(bad, cash("100000")).is_err());
    }
}
