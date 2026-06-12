//! Hyperliquid market-data ingestion.
//!
//! - [`ws`]: resilient WebSocket feed (reconnect/backoff, heartbeats,
//!   staleness watchdog, bounded delivery with drop accounting).
//! - [`hl`]: tolerant parsing of exchange messages into normalized events.
//! - [`book`]: order-book maintenance with integrity counters.
//! - [`recorder`]: length-prefixed on-disk event logs (truncation-detecting).

pub mod book;
pub mod hl;
pub mod recorder;
pub mod ws;
