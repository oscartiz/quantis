//! Risk layer: position sizing, pre-trade veto checks, drawdown limits, and a
//! kill switch.
//!
//! Two responsibilities, deliberately separated:
//!
//! - [`sizing`]: *how much* to trade — volatility targeting and capped
//!   fractional Kelly. These take f64 research inputs (estimated volatility and
//!   edge) and return a fixed-point [`quantis_core::types::Qty`]. They are
//!   advisory: they propose a size.
//! - [`gate`]: *whether* a proposed order may pass — integer pre-trade checks
//!   that can veto any order, a portfolio drawdown limit, and a kill switch.
//!   The gate is the authority; nothing reaches a venue without its approval.
//!
//! The split matters: sizing can be wrong (a bad volatility estimate) without
//! being dangerous, because the gate independently bounds position, notional,
//! and drawdown regardless of what sizing proposed.

pub mod gate;
pub mod sizing;

pub use gate::{RiskDecision, RiskGate, RiskLimits, RiskVeto};
pub use sizing::{capped_fractional_kelly, vol_target_qty};
