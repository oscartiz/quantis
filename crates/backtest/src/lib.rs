//! Event-driven backtesting engine.
//!
//! - [`fill`]: the matching engine — the **single source of truth** for fill
//!   logic; the Phase 4 paper gateway consumes this same code.
//! - [`strategy`]: the strategy trait and the SMA-cross plumbing demo.
//! - [`engine`]: the event loop tying book, strategy, fills, and accounting.
//! - [`report`]: seeded, hashed results artifacts.
//! - [`synthetic`]: deterministic synthetic streams for tests/benches only.

pub mod engine;
pub mod fill;
pub mod report;
pub mod runner;
pub mod strategy;
pub mod synthetic;
