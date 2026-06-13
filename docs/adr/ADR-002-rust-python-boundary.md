# ADR-002: The Rust/Python boundary, justified with numbers

- Status: accepted
- Date: 2026-06-12

## Context

Quantis is a two-language system and that choice must earn its complexity:
PyO3 bindings, two toolchains, two test suites. The claim to verify is that
the hot path (event loop, order book, fills) materially benefits from Rust,
while research (models, evaluation, plotting) belongs in Python.

The latency target set at project start: ms-class live path (Hyperliquid
blocks land at sub-second cadence, so sub-ms live latency buys nothing for
regime strategies), but **µs-scale backtest event loop**, because backtest
throughput is research iteration speed — walk-forward sweeps run the same
loop thousands of times.

## Measurements

Apple Silicon dev box, rustc 1.95 (`--release`, thin LTO), CPython 3.11.
Rust: Criterion (`make bench`); Python: best-of-5 wall timing
(`benchmarks/book_bench.py`), pure CPython by design — an event-driven loop
with path-dependent state (position, rolling windows, conditional orders)
does not vectorize cleanly, so idiomatic interpreted code *is* the realistic
alternative.

| Workload | Rust | Python | Ratio |
|---|---|---|---|
| Apply 20-level L2 snapshot to book | 34.5 ns | 2,225 ns | ~64× |
| Apply single level update | 3.8 ns | 52 ns | ~14× |
| Full backtest loop, per event¹ | **71 ns** | ~142 ns² | — |
| Honest end-to-end estimate, per event | 71 ns | ≳2,400 ns | **~34×** |

¹ book apply + strategy (SMA 120/600) + fill walking + accounting + timing
instrumentation, 120k synthetic events: 8.56 ms total = 14.0M events/s.
² the Python loop does strictly *less* work (rolling sums and position flips
only — no book maintenance, no fill walking) and is still 2× slower per
event; adding the snapshot apply alone puts the comparable figure above
2.4 µs.

What the ratio means in practice: one year of BTC L2 snapshots at ~2/s is
~63M events. One pass: Rust ~4.5 s, Python ~2.5 min. A 1,000-run
walk-forward parameter sweep: ~75 minutes vs ~2 days. The first is an
interactive research session; the second is an overnight batch job.

## Decision

- **Rust owns**: ingestion, normalization, order book, event logs, the
  matching/fill engine, the backtest loop, execution, risk checks.
- **Python owns**: feature pipelines, regime models, cross-validation,
  statistical evaluation, dashboards — code where iteration speed of the
  *researcher* dominates and per-event cost is irrelevant.
- The boundary is the event log and (from Phase 2) PyO3 bindings: Python
  *configures and launches* Rust runs and consumes their artifacts; it never
  sits inside the per-event loop.

## Alternatives considered

- **Pure Python (+ NumPy/Numba)** — NumPy can't express the path-dependent
  loop; Numba can approach native speed but trades away exact integer
  fixed-point semantics, deterministic hashing guarantees, and the shared
  backtest/live fill code that is the core integrity claim of this project.
- **Pure Rust** — gives up the scientific Python ecosystem (hmmlearn to
  validate the hand-rolled HMM, statsmodels, plotting); regime-model
  research would slow by far more than the loop speedup is worth.
- **Cython/C++ extension inside a Python engine** — same FFI complexity as
  PyO3 with a less safe language and without a coherent place for the live
  engine to run standalone.

## Consequences

Two toolchains and a bindings crate to maintain; contributors need both
ecosystems. Accepted: the measured 34× on the loop is the difference between
interactive research and batch research, and the live engine needs a
single-binary, GC-free runtime anyway. The cost is contained by keeping the
boundary narrow (artifacts + a small PyO3 surface).

## Appendix: order-book ladder choice

Same benchmark run, contiguous `Vec` ladders vs `BTreeMap`:
snapshot application 34.5 ns vs 458 ns (13×, and snapshots are Hyperliquid's
actual feed shape); single-level updates 3.8 ns vs 4.6 ns (1.2×). The
production book is the `Vec` ladder; `BTreeBook` stays in-tree as the
benchmark comparison subject and for venues with delta feeds where its
characteristics may differ at depth.
