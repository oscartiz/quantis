# data/

- `sample/` — committed, small, hash-pinned slice of BTC market data used by
  the offline demo and the CI smoke backtest; provenance notes (capture
  window, source, integrity hashes) in [`sample/PROVENANCE.md`](sample/PROVENANCE.md).
- `capture/` — gitignored, machine-local output of `quantis record`: the
  event logs that feed backtests.
