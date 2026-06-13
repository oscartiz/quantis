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
  A standing [`docs/losing-money.md`](docs/losing-money.md) explains what would
  lose money in production and why.
- **A measured language boundary.** Rust is used where benchmarks justify it,
  not where it is fashionable; [ADR-002](docs/adr/ADR-002-rust-python-boundary.md)
  records the Rust event loop at ~14M events/s, ~34× an equivalent Python loop.

## The honest result

The out-of-sample holdout — last 20% of the candle history, **sealed and
hashed before evaluation, evaluated exactly once** — covered a bear market
(Oct 2025 → Jun 2026). The causal regime strategy returned **+19.9% at 13%
exposure (Sharpe +1.40, max drawdown 9.5%)** while buy-and-hold lost −42.7%
(Sharpe −1.84, 50.6% drawdown). Read honestly: that is N = 1 in exactly the
risk-off environment the strategy is built for — in the bull-dominated
*in-sample* period it **trailed** buy-and-hold (Sharpe 0.32 vs 0.65). This is a
risk-reducing regime filter, not a demonstrated edge, and the credibility is in
the discipline that produced the number — see
[docs/statistical-honesty.md](docs/statistical-honesty.md).

## Status — all phases complete

| Phase | Scope | State |
|-------|-------|-------|
| 0 | Scaffolding, fail-closed config system, CI, secret scanning | ✅ done |
| 1 | WS ingestion, order book, backtest core, benchmarks, sample data | ✅ done |
| 2 | PyO3 bindings, feature pipeline, Gaussian HMM vs. BOCPD, purged CV | ✅ done |
| 3 | Realistic fills (fees, latency, funding), risk layer, DSR/SPA | ✅ done |
| 4 | Paper/testnet execution, Prometheus + Grafana, chaos test | ✅ done |
| 5 | Research dashboard, docs, one-shot holdout evaluation | ✅ done |

## Quickstart

Prerequisites: a stable Rust toolchain (`rustup`) and [`uv`](https://docs.astral.sh/uv/).

```sh
make setup   # Python env, build the Rust extension, pre-commit hooks
make demo    # seeded backtest + render the research dashboard, offline (<5 min)
make ci      # everything CI runs: fmt, clippy -D warnings, mypy strict, all tests
make smoke   # the backtest, asserting the committed determinism hash
make bench   # Criterion benchmarks (book ladders, backtest loop)
```

`make demo` runs a seeded backtest on a real 15-minute Hyperliquid BTC capture
(`data/sample/`, recorded by this repo's own ingestion code), writes a hashed
results artifact, and renders `results/dashboard.html` — all offline, no account
needed, in seconds. The demo strategy is an SMA crossover that exists to
exercise the engine, not to make money; on the sample it loses ~$2.82 over 15
minutes, mostly to fees, and this README says so on purpose.

Paper trading (offline replay or live):

```sh
quantis trade --config config/engine.toml --replay data/sample/btc-sample.qnts
quantis trade --config config/engine.toml --duration-secs 3600   # live, paper
uv run --project python python python/scripts/evaluate_holdout.py # the one-shot holdout
```

## Documentation

| Doc | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | C4 views + backtest/live/research data flow |
| [docs/statistical-honesty.md](docs/statistical-honesty.md) | leakage, CV, DSR/SPA, the one-shot holdout |
| [docs/losing-money.md](docs/losing-money.md) | what would lose money, with quantified sensitivities |
| [docs/runbook.md](docs/runbook.md) | operating the live engine: start / monitor / kill / recover |
| [docs/backtest-paper-gap.md](docs/backtest-paper-gap.md) | measured backtest↔paper gap and its causes |
| [docs/scaling.md](docs/scaling.md) | one → many: instruments, data fidelity, strategies, venues |
| [docs/adr/](docs/adr/) | architecture decision records (the Rust/Python split, regime models, fills, risk, gateways) |

## Layout

```
crates/core         shared domain types; fail-closed TOML engine config
crates/market-data  Hyperliquid WS client, order book, event recorder
crates/backtest     event-driven engine; THE fill/matching engine
crates/execution    order state machine, paper/testnet gateways, reconciliation, metrics
crates/risk         pre-trade gate, sizing, drawdown limits, kill switch
crates/python       PyO3 bindings exposing the core as `quantis_core`
crates/cli          `quantis` binary: record / replay / backtest / trade
python/quantis      research layer: data, features, models, evaluation, dashboard
config/             schema-validated example configs (TOML engine, YAML research)
data/sample/        committed, hash-pinned datasets (L2 capture + candle history)
docs/, docs/adr/    architecture, runbook, scaling, honesty docs + decision records
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

## Known limitations & future work

Stated plainly, because hiding them is the failure mode this project exists to
avoid. Fuller treatment in [docs/losing-money.md](docs/losing-money.md) and
[docs/scaling.md](docs/scaling.md).

- **Latency and queue position are lower bounds.** The market-data sample is a
  ~2/s snapshot feed, so sub-500ms latency is below resolution and resting-order
  queue position is unobservable. The maker fill path is specified
  (conservative back-of-queue) but not yet exercised by a maker strategy.
  *Future:* deep-L2/L3 backfill from Hyperliquid's S3 archive or a vendor.
- **Single asset, single venue, single strategy demonstrated.** The event model
  and risk layer are built multi-asset, but the shown results are BTC-only; the
  portfolio risk aggregate and cross-asset regime research are designed, not
  built.
- **Testnet order placement is a gated seam.** The request format is built and
  tested, but signing (msgpack + EIP-712) requires an operator key and is
  intentionally not shipped in-repo (ADR-006). No live-exchange round trip has
  been verified here.
- **The holdout is N = 1.** One favourable 8-month bear window is reported
  honestly as such — not an edge. A walk-forward refit across many windows is
  the next rigorous step.
- **The demo strategy is not alpha.** Both the SMA-cross (execution demo) and
  the regime filter (research demo) exist to exercise the machinery; neither is
  presented as a money-maker.

## License

MIT — see [LICENSE](LICENSE).
