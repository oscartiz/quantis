# Quantis — Progress Ledger

> Single source of truth for resumption. If a session is cut off, resume from
> **NEXT ACTION** below. Never regenerate files marked [DONE]. If a file was
> interrupted mid-write, discard the fragment and regenerate it whole.

## Status

- **Current phase:** Plan approved internally; Phase 0 not started.
- **Sub-step:** Awaiting user go-ahead to begin Phase 0.

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

- Repo lives at `/Users/tiz/Code/Repos/quantis`; user pushes to GitHub themselves
  (CI workflows are written but first run happens on push).
- Toolchain: stable Rust (2021 edition), Python 3.11+, maturin for bindings,
  uv for Python env. macOS/Apple Silicon is the dev box; CI runs Linux.
- License: MIT OR Apache-2.0 (Rust-ecosystem convention).
- Engine config: TOML; research/feature configs: YAML (pydantic-validated).
- Regime model pair: hand-rolled Gaussian HMM (EM, tested against hmmlearn) vs.
  Bayesian Online Changepoint Detection (Adams–MacKay, Student-t predictive).
- Prices/sizes are fixed-point i64 ticks in the Rust core (determinism, exact
  hashing of results artifacts); floats only at the research boundary.
- No GPU dependency in the core; PyTorch is an optional research extra.
- Hyperliquid testnet account/keys needed only at Phase 4; everything earlier
  runs offline. Hyperliquid API specifics (WS message schemas, candle pagination
  limits) are verified against live docs at Phase 1 start, not assumed.

## Phase checklist

File-level checklists are expanded at the start of each phase; later phases stay
coarse until then.

### Phase 0 — Foundations [TODO]
- [TODO] Cargo workspace + crate skeletons (`core`, `market-data`, `backtest`, `execution`, `risk`, `python`, `cli`)
- [TODO] Python package skeleton (`quantis/` with `data`, `features`, `models`, `evaluation`, `dashboard`)
- [TODO] Config system: TOML engine config + YAML research config, both schema-validated
- [TODO] CI: GitHub Actions (rust fmt/clippy/test, python ruff/mypy/pytest, seeded smoke backtest)
- [TODO] Pre-commit hooks incl. secret scan (gitleaks)
- [TODO] README skeleton, LICENSE, CONTRIBUTING.md, .gitignore, Makefile
- [TODO] docs/adr/ scaffold + ADR-000 (record architecture decisions)

### Phase 1 — Rust data + book + backtest core [TODO]
- Event model, fixed-point types, WS ingestion w/ reconnect + gap detection,
  order book reconstruction, event recorder, backtest loop v0, results artifact
  (config hash + git SHA + metrics), Criterion benches + Python comparison bench,
  bundled sample data, ADR-001 (Rust/Python split, with numbers).

### Phase 2 — PyO3 bindings + research layer + regime models [TODO]
- maturin bindings, data loaders, YAML-driven feature pipeline, Gaussian HMM (own EM),
  BOCPD, walk-forward + purged k-fold w/ embargo + leakage canary tests,
  holdout wall (hash committed, untouched), ADR-002 (regime-model selection).

### Phase 3 — Realistic fills + risk + statistical evaluation [TODO]
- Fill model v1 (maker/taker fees, queue approximation, latency injection, book-walk
  slippage, funding), risk crate (vol targeting, capped Kelly, stops, drawdown limits,
  kill switch, pre-trade veto API), DSR + SPA over logged trial history,
  docs/losing-money.md with quantified sensitivities, ADR-003 (fill model), ADR-004 (risk).

### Phase 4 — Paper/testnet execution + observability + chaos [TODO]
- Order state machine w/ idempotent client IDs, paper gateway sharing backtest matching
  code, testnet gateway, reconciliation loop, tracing + Prometheus + Grafana JSON,
  documented chaos test (feed kill mid-order), backtest-vs-paper gap report.

### Phase 5 — Dashboard + docs + polish [TODO]
- Static HTML research dashboard (equity, regime overlay, rolling Sharpe/Sortino,
  drawdown, exposure, per-regime attribution), README final w/ <5-min one-command demo,
  architecture.md (C4), runbook.md, scaling.md, statistical-honesty doc, holdout
  evaluated exactly once, known-limitations section.

## Pending decisions

- Order-book ladder data structure (BTreeMap vs. sorted-vec): decided by benchmark
  in Phase 1, recorded in ADR-001 appendix.
- SPA (Hansen) vs. White's Reality Check: pick in Phase 3 based on trial-log shape.

## NEXT ACTION

On **"CONTINUE"**: begin Phase 0. Expand the Phase 0 checklist to exact file paths,
then write, in order: workspace `Cargo.toml` → crate skeletons → `pyproject.toml` +
Python package skeleton → config schemas + example configs → Makefile →
`.github/workflows/ci.yml` → pre-commit + gitleaks config → README/LICENSE/
CONTRIBUTING/.gitignore → ADR-000 → update this ledger → commit (conventional commits,
one per coherent unit).
