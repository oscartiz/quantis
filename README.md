# Quantis

A research-to-execution engine for regime-switching strategies on Hyperliquid
perpetual futures: a Rust hot path (market-data ingestion, order-book
reconstruction, event-driven backtesting, paper/testnet execution) under a
Python research layer (feature pipelines, regime models, statistically honest
evaluation), sharing one matching engine so backtests and live paper trading
cannot disagree about fill logic.

> ⚠️ **Safety posture.** Quantis is research software, not financial advice,
> and trading perpetual futures carries substantial risk of loss. This
> codebase trades **paper/testnet only**: `mode = "mainnet"` is rejected at
> the configuration layer by design, and there is intentionally no flag to
> bypass it. Secrets never enter the repo — keys live in environment
> variables or gitignored config, enforced by a pre-commit secret scan.

## Why this exists

- **One matching engine.** The backtester's fill logic *is* the paper
  trader's fill logic (`crates/backtest`, consumed by `crates/execution`).
  Backtest/live divergence can then only come from data and latency — which
  gets measured and reported, not hidden.
- **Statistical honesty as a feature.** Purged walk-forward CV with embargo,
  leakage canary tests, multiple-testing corrections (Deflated Sharpe, SPA)
  over the *logged* trial history, and a holdout set evaluated exactly once.
  A standing `docs/losing-money.md` (Phase 3) explains what would lose money
  in production and why.
- **A measured language boundary.** Rust is used where benchmarks justify it,
  not where it is fashionable; ADR-002 (Phase 1) records Criterion numbers
  for the Rust event loop against an equivalent Python implementation.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 0 | Scaffolding, fail-closed config system, CI, secret scanning | ✅ done |
| 1 | WS ingestion, order book, backtest core, benchmarks, sample data | ✅ done |
| 2 | PyO3 bindings, feature pipeline, Gaussian HMM vs. BOCPD, purged CV | ✅ done |
| 3 | Realistic fills (fees, latency, funding), risk layer, DSR/SPA | ✅ done |
| 4 | Paper/testnet execution, Prometheus + Grafana, chaos test | ⬜ next |
| 5 | Research dashboard, docs, one-shot holdout evaluation | ⬜ |

## Quickstart

Prerequisites: a stable Rust toolchain (`rustup`) and [`uv`](https://docs.astral.sh/uv/).

```sh
make setup   # Python env + pre-commit hooks
make ci      # everything CI runs: fmt, clippy -D warnings, mypy strict, all tests
make demo    # seeded backtest on the bundled real BTC sample, offline
make smoke   # the same backtest, asserting the committed determinism hash
make bench   # Criterion benchmarks (book ladders, backtest loop)
```

`make demo` replays a real 15-minute Hyperliquid BTC capture
(`data/sample/`, recorded by this repo's own ingestion code) through the
backtest engine and writes a hashed results artifact — offline, no account
needed. The demo strategy is an SMA crossover that exists to exercise the
engine, not to make money; on the sample it loses ~$2.82 over 15 minutes,
mostly to fees, and the README says so on purpose. By Phase 5 the same demo
also renders the research dashboard, in under five minutes.

## Layout

```
crates/core         shared domain types; fail-closed TOML engine config
crates/market-data  Hyperliquid WS client, order book, event recorder   (Phase 1)
crates/backtest     event-driven engine; THE fill/matching engine       (Phase 1/3)
crates/execution    order state machine, paper/testnet gateways         (Phase 4)
crates/risk         pre-trade checks, sizing, drawdown limits           (Phase 3)
crates/python       PyO3 bindings exposing the core as `quantis_core`   (Phase 2)
crates/cli          `quantis` binary: record / replay / backtest / trade
python/quantis      research layer: data, features, models, evaluation, dashboard
config/             schema-validated example configs (TOML engine, YAML research)
docs/adr/           architecture decision records
PROGRESS.md         live build ledger: phase status, assumptions, next action
```

## Design principles

1. **Fail closed.** Unknown config keys are errors; anything not explicitly
   allowed (above all, live trading) is rejected.
2. **Reproducible by construction.** Every run is seeded and config-driven;
   results ship as hashed artifacts (config + git SHA + metrics) from Phase 1.
3. **No magic numbers in code.** Every parameter lives in versioned config.
4. **Honest documentation.** ADRs record alternatives and trade-offs;
   limitations are stated, not buried.

## License

MIT — see [LICENSE](LICENSE).
