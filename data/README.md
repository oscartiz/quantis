# data/

- `sample/` — committed, small, hash-pinned slice of BTC market data used by
  the offline demo and the CI smoke backtest. Lands in Phase 1 together with
  provenance notes (capture window, source, integrity hash).
- `capture/` — gitignored, machine-local output of `quantis record` (Phase 1):
  the event logs that feed backtests.
