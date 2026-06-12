//! Order execution (lands in Phase 4).
//!
//! Will contain: an order state machine with idempotent client order IDs, a
//! paper gateway that reuses `quantis-backtest`'s matching engine against live
//! data, a Hyperliquid **testnet** gateway, position tracking, and a
//! reconciliation loop against exchange state.
//!
//! A mainnet gateway is intentionally absent from this codebase; the config
//! layer rejects `mode = "mainnet"` with no bypass flag.
