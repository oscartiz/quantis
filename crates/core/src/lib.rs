//! Core domain types shared by every Quantis crate.
//!
//! Phase 0 ships the validated engine configuration. Phase 1 adds the event
//! model (trades, L2 deltas, book snapshots), fixed-point price/size types,
//! and clock/latency instrumentation.

pub mod config;
