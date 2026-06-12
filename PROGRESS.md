# Quantis — Progress Ledger

> Single source of truth for resumption. If a session is cut off, resume from
> **NEXT ACTION** below. Never regenerate files marked [DONE]. If a file was
> interrupted mid-write, discard the fragment and regenerate it whole.

## Status

- **Current phase:** Phase 0 — Foundations [IN PROGRESS]
- **Sub-step:** writing scaffolding files (hygiene → Rust → Python → CI → docs)

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
- License: **MIT** (narrowed from "MIT OR Apache-2.0" to cut boilerplate; trivial
  to dual-license later if ever needed).
- Engine config: TOML; research/feature configs: YAML (pydantic-validated). Both
  fail closed: unknown fields are errors. → ADR-001.
- ADR numbering is chronological by decision date: 000 process, 001 config split,
  002 Rust/Python boundary (Phase 1), 003 regime models (Phase 2), 004 fill model
  (Phase 3), 005 risk framework (Phase 3).
- Regime model pair: hand-rolled Gaussian HMM (EM, tested against hmmlearn) vs.
  Bayesian Online Changepoint Detection (Adams–MacKay, Student-t predictive).
- Prices/sizes are fixed-point i64 ticks in the Rust core (determinism, exact
  hashing of results artifacts); floats only at the research boundary.
- No GPU dependency in the core; PyTorch is an optional research extra.
- Hyperliquid testnet account/keys needed only at Phase 4; everything earlier
  runs offline. Hyperliquid API specifics (WS message schemas, candle pagination
  limits) are verified against live docs at Phase 1 start, not assumed.

## Phase checklist

### Phase 0 — Foundations [IN PROGRESS]

Hygiene:
- [TODO] `.gitignore`, `.editorconfig`, `LICENSE` (MIT)

Rust:
- [TODO] `Cargo.toml` — workspace: members, shared deps, lints, profiles;
  `crates/python` excluded from default-members (needs local Python)
- [TODO] `crates/core/{Cargo.toml,src/lib.rs,src/config.rs}` — validated TOML
  engine config; `mode = "mainnet"` hard-rejected with a pointed error
- [TODO] `config/engine.example.toml` — commented example, parsed by core tests
- [TODO] `crates/{market-data,backtest,execution,risk}` — doc-only skeletons
- [TODO] `crates/python/{Cargo.toml,src/lib.rs,pyproject.toml}` — PyO3 skeleton
  module `quantis_core`, maturin-built, abi3-py311
- [TODO] `crates/cli/{Cargo.toml,src/main.rs}` — `quantis config validate` works;
  `record`/`replay`/`backtest`/`trade` are loud phase-labelled stubs

Python:
- [TODO] `python/pyproject.toml` (hatchling; ruff + mypy strict + pytest config),
  `python/.python-version`, `python/quantis/py.typed`
- [TODO] `python/quantis/{__init__.py,config.py}` — pydantic research config
  schema (seed, features, model, cv w/ embargo), fail-closed
- [TODO] `python/quantis/{data,features,models,evaluation,dashboard}/__init__.py`
- [TODO] `python/tests/test_config.py`, `config/research.example.yaml`

Tooling/CI:
- [TODO] `Makefile` — setup / fmt / fmt-check / lint / test / ci / demo
- [TODO] `.github/workflows/ci.yml` — rust (fmt, clippy -D warnings, test,
  check bindings), python (ruff, mypy, pytest), smoke (config validate;
  becomes the seeded deterministic backtest in Phase 1)
- [TODO] `.pre-commit-config.yaml` — gitleaks, ruff, cargo fmt, hygiene hooks

Docs:
- [TODO] `README.md` (safety posture, status, quickstart), `CONTRIBUTING.md`,
  `data/README.md` + `data/sample/.gitkeep`
- [TODO] `docs/adr/{README.md,template.md}`, ADR-000 (record decisions),
  ADR-001 (TOML engine config / YAML research config)

### Phase 1 — Rust data + book + backtest core [TODO]
Event model, fixed-point types, WS ingestion w/ reconnect + gap detection,
order book reconstruction, event recorder, backtest loop v0, results artifact
(config hash + git SHA + metrics), Criterion benches + Python comparison bench,
bundled sample data, ADR-002 (Rust/Python boundary, with numbers).

### Phase 2 — PyO3 bindings + research layer + regime models [TODO]
maturin bindings, data loaders, YAML-driven feature pipeline, Gaussian HMM (own EM),
BOCPD, walk-forward + purged k-fold w/ embargo + leakage canary tests,
holdout wall (hash committed, untouched), ADR-003 (regime-model selection).

### Phase 3 — Realistic fills + risk + statistical evaluation [TODO]
Fill model v1 (maker/taker fees, queue approximation, latency injection, book-walk
slippage, funding), risk crate (vol targeting, capped Kelly, stops, drawdown limits,
kill switch, pre-trade veto API), DSR + SPA over logged trial history,
docs/losing-money.md with quantified sensitivities, ADR-004 (fill model), ADR-005 (risk).

### Phase 4 — Paper/testnet execution + observability + chaos [TODO]
Order state machine w/ idempotent client IDs, paper gateway sharing backtest matching
code, testnet gateway, reconciliation loop, tracing + Prometheus + Grafana JSON,
documented chaos test (feed kill mid-order), backtest-vs-paper gap report.

### Phase 5 — Dashboard + docs + polish [TODO]
Static HTML research dashboard (equity, regime overlay, rolling Sharpe/Sortino,
drawdown, exposure, per-regime attribution), README final w/ <5-min one-command demo,
architecture.md (C4), runbook.md, scaling.md, statistical-honesty doc, holdout
evaluated exactly once, known-limitations section.

## Pending decisions

- Order-book ladder data structure (BTreeMap vs. sorted-vec): decided by benchmark
  in Phase 1, recorded in ADR-002 appendix.
- SPA (Hansen) vs. White's Reality Check: pick in Phase 3 based on trial-log shape.

## NEXT ACTION

Continue Phase 0 in this order, verifying (`cargo fmt/clippy/test`, `uv run
ruff/mypy/pytest`) before each conventional commit:
hygiene files → Rust workspace + core config + CLI → Python package + research
config → Makefile + CI → pre-commit → README/CONTRIBUTING/ADRs → final ledger
update marking Phase 0 [DONE].
