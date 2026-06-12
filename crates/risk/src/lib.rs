//! Risk layer (lands in Phase 3).
//!
//! Will contain: volatility-targeted and capped fractional-Kelly position
//! sizing, per-trade stop logic, portfolio-level drawdown limits, a kill
//! switch, and a pre-trade check API with the authority to veto any order
//! before it reaches a gateway.
