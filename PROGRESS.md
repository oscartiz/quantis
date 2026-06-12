# Quantis — Progress Ledger

> Single source of truth for resumption. If a session is cut off, resume from
> **NEXT ACTION** below. Never regenerate files marked [DONE]. If a file was
> interrupted mid-write, discard the fragment and regenerate it whole.

## Status

- **Current phase:** Phase 0 — Foundations [DONE] (commit history tells the story)
- **Sub-step:** awaiting user go-ahead to begin Phase 1.

## Decisions locked (clarifying Q&A, 2026-06-11)

1. **Execution scope:** Paper + Hyperliquid **testnet only**. Mainnet gateway is a
   loudly-documented gated stub — no code path can touch real capital.
2. **Historical data:** Self-capture via the Rust recorder (L2 + trades) from day
   one; longer candle history from Hyperliquid's API; HL S3 deep-L2 backfill noted
   as optional future work. A small bundled sample dataset ships in-repo so the
   README demo is reproducible offline in <5 min.
3. **Latency regime:** ms-class live path; µs-scale per-event **backtest** loop
   (research iteration speed is where Rust pays). Honest Criterion numbers for both.
4. **Universe:** BTC perp only. Event model and risk layer designed multi-asset;
   research/demos single-asset.

## Standing assumptions (user may correct any time)

- Repo lives at `/Users/tiz/Code/Repos/quantis`; user pushes to GitHub themselves.
  CI workflows are committed; branch protection (block merge on red) is a GitHub
  setting the user enables on push.
- Toolchain verified locally: cargo 1.95 (edition 2024), uv 0.10, pyo3 0.26
  (builds against system Python 3.14; project pins 3.11 floor), maturin >=1.7.
- License: **MIT** (narrowed from "MIT OR Apache-2.0" to cut boilerplate).
- Engine config: TOML; research configs: YAML; both fail closed → ADR-001.
- ADR numbering is chronological: 000 process, 001 config split, 002 Rust/Python
  boundary (Phase 1), 003 regime models (Phase 2), 004 fill model (Phase 3),
  005 risk framework (Phase 3).
- Regime model pair: hand-rolled Gaussian HMM (EM, tested against hmmlearn) vs.
  Bayesian Online Changepoint Detection (Adams–MacKay, Student-t predictive).
- Prices/sizes are fixed-point i64 ticks in the Rust core (determinism, exact
  hashing of results artifacts); floats only at the research boundary.
- No GPU dependency in the core; PyTorch is an optional research extra.
- Hyperliquid testnet keys needed only at Phase 4; everything earlier runs
  offline. Hyperliquid API specifics (WS schemas, candle pagination limits) are
  verified against live docs at Phase 1 start, not assumed from memory.

## Phase checklist

### Phase 0 — Foundations [DONE]

- [DONE] `.gitignore`, `.editorconfig`, `LICENSE` (MIT)
- [DONE] `Cargo.toml` — workspace (7 crates), shared deps, workspace lints
  (missing_docs, clippy::all), release profile; `crates/python` excluded from
  default-members (needs a Python toolchain)
- [DONE] `crates/core` — `config.rs`: fail-closed TOML engine config;
  `mode = "mainnet"` rejected with a pointed error, no bypass; 5 unit tests
- [DONE] `config/engine.example.toml` — commented example, parsed by core tests
- [DONE] `crates/{market-data,backtest,execution,risk}` — doc-only skeletons
  stating scope and landing phase
- [DONE] `crates/python` — PyO3 0.26 skeleton module `quantis_core`
  (abi3-py311, maturin pyproject, extension-module feature-gated)
- [DONE] `crates/cli` — `quantis config validate` works end to end;
  record/replay/backtest/trade are loud phase-labelled stubs; 2 tests
- [DONE] `python/` — package `quantis` (hatchling, py.typed, mypy strict,
  ruff): pydantic research config schema + 5 subpackage skeletons; 6 tests
- [DONE] `config/research.example.yaml` — commented example, parsed by tests
- [DONE] `Makefile` — setup/fmt/fmt-check/lint/test/ci/demo
- [DONE] `.github/workflows/ci.yml` — rust, python, smoke jobs
- [DONE] `.pre-commit-config.yaml` — gitleaks, ruff, cargo fmt, hygiene hooks
- [DONE] `README.md` (safety posture first), `CONTRIBUTING.md`,
  `data/README.md`, `docs/adr/{README,template,ADR-000,ADR-001}.md`

Verified locally: `make ci` green (clippy -D warnings, mypy strict, 7 Rust
tests, 6 Python tests), `make demo` validates the example config end to end,
`cargo check -p quantis-python` compiles the bindings crate.

### Phase 1 — Rust data + book + backtest core [TODO]

Expand to exact file paths at phase start. Scope:
- Verify Hyperliquid WS/REST API schemas against live docs (first sub-step).
- `core`: event model (Trade, L2Update, BookSnapshot, Candle, order/fill
  events), fixed-point i64 price/size types, exchange-vs-receive timestamps.
- `market-data`: tokio WS client w/ reconnect + backoff + jitter, gap
  detection → resnapshot, bounded channels w/ drop accounting, recorder.
- Order book reconstruction; ladder structure picked by Criterion benchmark.
- `backtest`: event-driven loop v0, strategy trait, top-of-book fills + fees,
  seeded results artifact (config hash + git SHA + metrics JSON).
- Benches: book-apply throughput, backtest p50/p95/p99 per event, equivalent
  Python loop for comparison → ADR-002 with numbers.
- Bundle `data/sample/` slice + provenance + hash; upgrade CI smoke job to
  the seeded backtest with asserted artifact hash.
- CLI: `record`, `replay`, `backtest` become real.

### Phase 2 — PyO3 bindings + research layer + regime models [TODO]
maturin bindings (backtest runner, event-log readers), data loaders,
YAML-driven feature pipeline, Gaussian HMM (own EM, validated vs. hmmlearn),
BOCPD, walk-forward + purged k-fold w/ embargo + leakage canary tests,
holdout wall (hash committed, untouched), ADR-003.

### Phase 3 — Realistic fills + risk + statistical evaluation [TODO]
Fill model v1 (maker/taker fees, queue approximation, latency injection,
book-walk slippage, funding), risk crate (vol targeting, capped Kelly, stops,
drawdown limits, kill switch, pre-trade veto API), DSR + SPA over logged trial
history, docs/losing-money.md with quantified sensitivities, ADR-004, ADR-005.

### Phase 4 — Paper/testnet execution + observability + chaos [TODO]
Order state machine w/ idempotent client IDs, paper gateway sharing backtest
matching code, testnet gateway, reconciliation loop, tracing + Prometheus +
Grafana JSON, documented chaos test (feed kill mid-order), backtest-vs-paper
gap report.

### Phase 5 — Dashboard + docs + polish [TODO]
Static HTML research dashboard, README final w/ <5-min one-command demo,
architecture.md (C4), runbook.md, scaling.md, statistical-honesty doc, holdout
evaluated exactly once, known-limitations section.

## Pending decisions

- Order-book ladder data structure (BTreeMap vs. sorted-vec): decided by
  benchmark in Phase 1, recorded in ADR-002 appendix.
- SPA (Hansen) vs. White's Reality Check: pick in Phase 3 based on trial-log
  shape.

## NEXT ACTION

On **"CONTINUE"**: begin Phase 1. First sub-step: verify Hyperliquid WS/REST
API message schemas and candle endpoint limits against the live docs, then
expand the Phase 1 checklist to exact file paths and start with the event
model in `crates/core` (fixed-point types first, then events), test-first.
