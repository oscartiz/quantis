# Quantis — Progress Ledger

> Single source of truth for resumption. If a session is cut off, resume from
> **NEXT ACTION** below. Never regenerate files marked [DONE]. If a file was
> interrupted mid-write, discard the fragment and regenerate it whole.

## Status

- **Current phase:** Phase 5 — dashboard + docs + one-shot holdout [DONE]
- **Project status: ALL PHASES COMPLETE (0–5).**

### Phase 5 — all [DONE]
- [DONE] Bundled 3.5y BTC daily candles (HL, hash-pinned) as the regime dataset.
- [DONE] `GaussianHMM.filter_proba` (causal forward-only inference) + causality test.
- [DONE] `quantis/data/candles.py` loader; `quantis/dashboard/report.py` static
  self-contained HTML (regime overlay, equity vs hold, rolling Sharpe/Sortino,
  drawdown, exposure, per-regime attribution). `make demo` renders it in <5s.
- [DONE] Holdout sealed + manifest committed BEFORE evaluation; `evaluate_holdout.py`
  fits on research, reveals holdout once. Result: bear holdout, strategy +19.9%
  at 13% exposure (Sharpe +1.40) vs hold -42.7% — reported honestly as N=1.
- [DONE] docs: architecture.md (C4), scaling.md, statistical-honesty.md; README
  final pass + Known Limitations; ADR index complete (000–006).

**Verified end of Phase 5:** 84 Rust tests, 60 Python tests, fmt/clippy/mypy
clean, golden-hash smoke passes. `make demo` offline in <5s.

### Phase 4 — all [DONE]
- [DONE] `execution::order`/`manager`: idempotent order state machine (fills
  dedupe by fill id, cloids dedupe resubmits, validated transitions), position
  + realized PnL with integer VWAP.
- [DONE] `execution::paper`: PaperGateway reuses `quantis_backtest::FillEngine`
  + `quantis_risk::RiskGate`. Measured: paper equity change == backtest net PnL
  to the cent on the sample (docs/backtest-paper-gap.md).
- [DONE] `execution::testnet`: HL order action built + tested to documented
  format; `ActionSigner` seam, submission gated without a signer. ADR-006.
- [DONE] `execution::reconcile`: position drift detection (exchange authoritative).
- [DONE] `execution::metrics`: Prometheus text rendering; CLI serves /metrics
  via a dependency-free HTTP responder. docs/grafana-dashboard.json committed.
- [DONE] Chaos test (`tests/chaos.rs`): feed-kill + replay → no phantom position;
  genuine miss → drift caught.
- [DONE] `quantis trade` (replay + live) wired; docs/runbook.md.

**Verified end of Phase 4:** 84 Rust tests, 52 Python tests, fmt/clippy/mypy
clean, golden-hash smoke + chaos drill pass.

### Phase 3 — all [DONE]
- [DONE] Fill model v1: execution latency (orders fill at the first snapshot
  after `submit+latency`, never the signal snapshot) + funding accrual.
  Integer-exact; golden hash deliberately bumped (now `5c17d06c…`). ADR-004,
  which documents the honest latency-resolution ceiling (sub-500ms = no-op).
- [DONE] `risk` crate: vol-targeting + capped fractional Kelly sizing (advisory,
  f64) and the integer `RiskGate` (pre-trade veto on position/notional caps,
  always-allow de-risking, drawdown-tripped latching kill switch). proptest
  property tests. ADR-005.
- [DONE] Python `evaluation`: Sharpe/Sortino/maxDD, Probabilistic + Deflated
  Sharpe, Hansen SPA + White RC via stationary bootstrap, append-only TrialLog.
  Tests show DSR deflates a lucky winner and SPA separates edge from noise.
- [DONE] `docs/losing-money.md` with quantified sensitivities (fees ×1/×2/×3 →
  −2.82/−4.83/−6.84; latency sweep; regime/capacity/overfitting mechanisms).

**Verified end of Phase 3:** 64 Rust tests, 52 Python tests, clippy -D warnings
clean, mypy strict clean, golden-hash smoke passes (new hash committed).
Rebuild the extension after Rust changes: `make bindings`.

### Phase 2 slice plan — all [DONE]
- [DONE] A — maturin PyO3 bindings; `quantis_backtest::runner` shared by CLI +
  binding; Python-driven backtest reproduces the golden hash (3 cross-lang tests).
  `read_mid_series` exposes event logs to research (floats at the boundary only).
- [DONE] B — numpy feature pipeline (config-driven): log_return, realized_vol,
  sma, momentum, zscore; causal-by-construction; expanding-window-endpoint
  leakage check; canary test catches 2 deliberately-leaky features (7 tests).
- [DONE] C — Gaussian HMM (own log-space Baum-Welch EM + Viterbi), 3-state,
  diagonal covariance, regime_order() resolves label-switching. Validated vs
  hmmlearn from a SHARED init (the only fair test for a non-convex objective):
  means agree to 2e-4 (6 tests). `init=` param enables warm-start/oracle compare.
- [DONE] D — BOCPD (Adams–MacKay, NIG prior, Student-t predictive). Online/causal
  counterpart to the HMM; changepoints from run-length resets; causality asserted
  via prefix re-runs (7 tests).
- [DONE] E — walk-forward + purged k-fold CV with embargo; `assert_no_leakage`
  guard catches a naive unpurged k-fold (CV-level leakage canary; 7 tests).
- [DONE] F — holdout wall: build_manifest (seal), load_research (refuse holdout),
  reveal_holdout (gated + hash-verified). ADR-003 written (HMM + BOCPD selection).

Python deps: runtime numpy/pydantic/PyYAML; dev maturin/hmmlearn(+scipy,sklearn)/
mypy/ruff/pytest. mypy overrides: quantis_core, scipy.special, hmmlearn.*.
Build extension before pytest: `make bindings` (CI does this in the python job).

**Verified end of Phase 2:** 46 Rust tests, 43 Python tests, clippy -D warnings
clean, mypy strict clean, golden-hash smoke passes. Cross-language determinism
holds (Python-driven backtest == CLI hash).

## Post-build follow-ups

**The phased build (0–5) is complete.** Follow-ups beyond the brief:

- [DONE] Walk-forward refit across many windows → OOS distribution
  (`quantis.evaluation.walk_forward`, `scripts/walk_forward_eval.py`). Result:
  pooled OOS Sharpe 0.60 vs hold 0.20, but median window Sharpe 0.00 / 40%
  positive — episodic edge. Strengthened statistical-honesty.md §5.
- [DONE] A maker strategy exercising the conservative queue model end to end:
  `IntentKind::Limit`, `FillEngine::match_resting`, engine resting-order loop,
  `PassiveMaker`. Proven maker-only via a zero-maker-fee test. Market path /
  golden hash unchanged. ADR-004 updated.
- [TODO] Deep-L2/L3 backfill to lift the latency/queue resolution ceiling.
- [TODO] Multi-asset: portfolio risk aggregate + cross-asset regime research.
- [TODO] Wire the testnet `ActionSigner` with a real key; measure paper↔testnet
  gap (needs testnet keys; ADR-006).

## NEXT ACTION

On **"CONTINUE"** with no new instruction: implement a **maker (limit-order)
strategy** and wire the conservative back-of-queue fill model (ADR-004) into the
backtest engine + paper gateway, with tests, so the maker path is exercised end
to end rather than only specified. This is the highest-value fully-offline item
left. Otherwise: name a follow-up above or a new direction.

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

### Phase 1 — Rust data + book + backtest core [DONE]

- [DONE] Verified Hyperliquid WS schemas vs live docs (2026-06-11): l2Book is
  a full-snapshot feed (no sequence numbers), trades carry B/A side codes,
  heartbeat `{"method":"ping"}` vs 60s idle timeout, ms timestamps.
- [DONE] `core::types` — i64 1e8 fixed-point Px/Qty/Cash (exact parse from
  exchange decimal strings), TsNanos (exch ms widened, recv ns), Side; 14 tests.
- [DONE] `core::events` — MarketEvent {Trade, L2Snapshot, Candle}; instrument
  lives in the log header, not per-event.
- [DONE] `core::hash`, `core::stats` — SHA-256 helpers, nearest-rank percentiles.
- [DONE] `market-data::ws` — tokio WS, reconnect w/ capped jittered backoff,
  30s ping, 75s staleness watchdog, bounded channel w/ drop accounting.
  (No sequence-gap detection: snapshot feed has no sequence numbers — handled
  honestly via staleness + full resnapshot on reconnect.)
- [DONE] `market-data::hl` — tolerant parsing (unknown fields ignored, the
  deliberate inverse of fail-closed config); 6 tests.
- [DONE] `market-data::book` — Vec ladder (production) + BTreeBook (bench
  subject); repairs+counts crossed/unsorted/bad-qty/ts-regression; 5 tests.
- [DONE] `market-data::recorder` — length-prefixed bincode logs, truncation
  detected not absorbed; 3 tests.
- [DONE] `backtest::fill` — matching engine v0 (market orders walk visible
  ladder, ppm fees, unfilled reported); single source of truth. Limits stated
  in-module: no latency/queue/funding until Phase 3.
- [DONE] `backtest::strategy` — Strategy trait + SmaCross plumbing demo
  (integer cross-multiply, bit-reproducible); 4 tests.
- [DONE] `backtest::engine` — event loop, deterministic accounting separated
  from measured timing; determinism contract test; 2 tests.
- [DONE] `backtest::report` — split artifact (hashed deterministic section +
  unhashed runtime provenance); stable-hash test.
- [DONE] `backtest::synthetic` — seeded streams for tests/benches only (never
  used for performance claims).
- [DONE] Benches: `book.rs` (Vec vs BTree), `engine.rs` (full loop),
  `benchmarks/book_bench.py` (pure-CPython comparison). ADR-002 written.
- [DONE] CLI: `record`, `replay`, `backtest --expect-hash` all real.
- [DONE] Bundled real 15-min BTC capture (`data/sample/btc-sample.qnts`,
  3077 events, clean integrity) + `PROVENANCE.md`; golden hash in
  `tests/smoke/expected_hash.txt`; CI smoke job + cargo integration test both
  assert it; `.gitattributes` pins bytes for cross-platform reproducibility.

**Measured (Apple Silicon, release):** book snapshot-apply 34.5ns (Vec) vs
458ns (BTree); full backtest loop 71ns/event = 14M events/s; pure-CPython
equivalent ≳2.4µs/event → ~34× (ADR-002). Demo strategy on sample: −2.82 net
over 15 min (2.01 fees) — honest small loss for a labeled non-alpha demo.

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

- ~~Order-book ladder (BTreeMap vs sorted-vec)~~ — RESOLVED: Vec, ADR-002 appendix.
- SPA (Hansen) vs. White's Reality Check: pick in Phase 3 based on trial-log
  shape.
- Phase 2: confirm hmmlearn is acceptable as a *test oracle* dev-dependency
  (the shipped HMM is hand-rolled; hmmlearn only validates it).

## Toolchain notes (verified this phase)

- pyo3 0.26, tokio-tungstenite 0.29 (rustls + **ring** provider — must call
  `CryptoProvider::install_default()`; done in `ws::run_feed`).
- bincode 1.x (classic serde API), rand 0.9, criterion 0.8.
- Shell CWD can drift to `python/` between turns — always `cd` to repo root
  before git ops.

## NEXT ACTION

On **"CONTINUE"**: begin Phase 2. First sub-step: scaffold maturin build of
`crates/python` into the `quantis` package env, expose a `run_backtest(config_path)`
binding returning the artifact dict + an event-log reader yielding arrays, with
a cross-language test asserting the Python-driven backtest reproduces the same
determinism hash as the CLI. Then the YAML feature pipeline, then the Gaussian
HMM (own EM, validated vs hmmlearn), then BOCPD, then purged walk-forward CV
with embargo + leakage canary, then raise the holdout wall (commit its hash,
do not touch). ADR-003 records the regime-model selection.
