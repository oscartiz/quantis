//! Hyperliquid market-data ingestion (lands in Phase 1).
//!
//! Will contain: a WebSocket client with reconnect/backoff and jitter,
//! subscription management, sequence-gap detection with resnapshotting,
//! order-book reconstruction, bounded channels with drop accounting, and the
//! on-disk event recorder that feeds backtests.
