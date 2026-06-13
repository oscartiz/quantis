//! Core domain types shared by every Quantis crate.
//!
//! - [`config`]: fail-closed engine configuration (TOML).
//! - [`types`]: fixed-point prices/quantities/cash and timestamps.
//! - [`events`]: the normalized market event model.
//! - [`hash`]: SHA-256 helpers for data integrity and results artifacts.

pub mod config;
pub mod events;
pub mod hash;
pub mod stats;
pub mod types;
