//! Event-driven backtesting engine (lands in Phase 1; realistic fills Phase 3).
//!
//! Houses the matching/fill engine that is the **single source of truth** for
//! fills: the paper-trading gateway in `quantis-execution` consumes this same
//! code, so backtest and paper results can only diverge through data and
//! latency — never through duplicated fill logic.
