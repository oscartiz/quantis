# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-07-02

Initial public release: the complete phased build (0–5) plus the post-build
research follow-ups. See `PROGRESS.md` for the full ledger and `docs/adr/` for
the decisions.

### Added

- **Rust workspace** (`crates/`): shared domain types with fixed-point i64
  prices and fail-closed TOML config (`core`); Hyperliquid WebSocket ingestion,
  order-book reconstruction, and event recording (`market-data`); an
  event-driven backtesting engine that is the single source of truth for fill
  logic — fees, execution latency, funding, and a conservative maker queue
  model (`backtest`); an idempotent order state machine with paper/testnet
  gateways, reconciliation, and Prometheus metrics (`execution`); pre-trade
  risk checks, volatility-target and capped-Kelly sizing, and a latching
  drawdown kill switch (`risk`); the `quantis` CLI for record / replay /
  backtest / trade (`cli`); PyO3 bindings exposing the core as `quantis_core`
  (`python`).
- **Python research layer** (`python/quantis`): config-driven causal feature
  pipeline with leakage canaries; hand-rolled Gaussian HMM (validated against
  hmmlearn) and BOCPD regime models; purged walk-forward CV with embargo;
  Deflated Sharpe, Hansen SPA, CSCV/PBO overfitting diagnostics and a global
  multiple-testing correction over all logged trials; a sealed, hash-pinned
  holdout evaluated exactly once; static HTML research dashboard and report.
- **Safety posture**: `mode = "mainnet"` rejected at the config layer with no
  bypass; secret scanning (gitleaks) in pre-commit; paper/testnet only.
- **Reproducibility**: seeded, deterministic backtests with a committed golden
  hash smoke test; hash-pinned sample data (15-min BTC L2 capture, 3.5y daily
  candles, funding history) so every demo and study runs offline.
- **Docs**: README with honest results, architecture (C4), runbook,
  statistical-honesty methodology, losing-money analysis, scaling notes, and
  ADRs 000–010.
- **CI**: GitHub Actions running Rust fmt/clippy/tests, Python
  ruff/mypy-strict/pytest (including cross-language determinism tests), and
  the golden-hash smoke job.

[0.1.0]: https://github.com/oscartiz/quantis/releases/tag/v0.1.0
