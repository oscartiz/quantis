# ADR-009: Overfitting diagnostics — CSCV/PBO and a global trial correction

- Status: accepted
- Date: 2026-06-17

## Context

The repo already deflates each search (Deflated Sharpe) and bootstrap-tests the
winner (SPA / Reality Check). Two honesty gaps remained:

1. **No direct measure of selection fragility.** DSR and SPA ask "is the best
   config's performance distinguishable from luck?" Neither answers the blunter,
   complementary question: *when I pick the in-sample-best config, how often is it
   merely mediocre — or worse — out of sample?* That is the question a
   practitioner actually faces when they deploy "the best backtest".
2. **Per-search correction only.** Each study deflated *within itself*. But the
   project has now run four searches (regime configs, the BOCPD overlay, sizing,
   long/short). Reporting "the best result across all four" while only correcting
   each search in isolation leaves the **cross-experiment** selection
   uncorrected — exactly the multiple-testing leak the repo exists to close.

## Decision

Add two diagnostics, both wired into every study and a consolidated report.

### CSCV / PBO (`quantis.evaluation.cscv.cscv_pbo`)

Combinatorially Symmetric Cross-Validation (Bailey, Borwein, López de Prado &
Zhu, 2014). Given the `(T, N)` matrix of per-period performance for the `N`
searched configs: cut `T` into `S` contiguous blocks; for each of the `C(S, S/2)`
symmetric IS/OOS splits, rank configs by IS performance, take the IS winner, and
record its OOS relative rank `ω` and logit `λ = ln(ω/(1-ω))`. **PBO = fraction of
splits where the IS winner is OOS at or below the median** (`λ ≤ 0`). It needs no
distributional assumption and no benchmark — it is a direct combinatorial
measurement. PBO near 0.5 (or above) means selection is overfitting; near 0 means
it is robust.

### Global trial correction (`scripts/research_report.py`)

A consolidated report pools **every** logged trial across all four studies and
deflates the single best trial found *anywhere* by the expected maximum Sharpe
over the entire union. This corrects the cross-experiment selection that
per-study deflation misses. It renders one self-contained
`results/research-report.html`; `make research` runs the studies then the report.

## Result (reported as-is)

Per-study PBO and DSR (OOS-within-research, net of funding):

| study | trials | Deflated Sharpe | PBO |
|---|---:|---:|---:|
| Regime-model search | 18 | 0.613 | **0.623** |
| HMM + BOCPD overlay | 9 | 0.682 | **0.921** |
| Position sizing (vol-target + Kelly) | 16 | 0.673 | 0.369 |
| Long/short (short the bear) | 9 | 0.551 | **0.774** |

PBO immediately earns its place: the **overlay** search (0.92) and the
**long/short** search (0.77) are revealed as strongly overfit — their
in-sample-best configs are OOS coin-flips-or-worse — while the **sizing** search
(0.37) is comparatively robust (conviction weighting carries real, if
non-significant, OOS structure). PBO and DSR agree in spirit and add detail
neither has alone.

The global correction is the capstone: across **all 52 trials in the four
studies**, the best configuration anywhere (`hmm_k4_vol15`, per-period Sharpe
0.061) is deflated by the expected-max-Sharpe over the union (0.045) to a
**global Deflated Sharpe of 0.661 → no edge survives**. Even pooling every
experiment the project has run, nothing clears the bar — the most honest possible
statement of the repo's central finding.

## Alternatives considered

- **Walk-forward alone as the overfitting check.** It is necessary but not
  sufficient: a single forward path does not measure how *fragile the selection*
  is across recombinations of the timeline. CSCV does, and complements the
  existing walk-forward harness.
- **A single combined search instead of a global correction.** Re-running every
  config in one giant grid would lose the per-study structure (each search asks a
  different question against a different benchmark). Pooling the logged trials
  post-hoc gives the union correction without collapsing the studies.
- **PBO over the union matrix.** The studies have different aligned OOS lengths
  (regime/short align to a common date range; ensemble/sizing use a fixed
  window), so a single union `(T, N)` matrix is not clean. The union uses the
  DSR-style expected-max deflation (which needs only per-trial Sharpes, not
  aligned `T`); PBO is reported per study, where the matrix is well-defined.

## Consequences

- Every study now prints PBO alongside DSR/SPA, and `make research` produces one
  HTML with per-study and global verdicts — the research story in one artifact.
- The global correction makes cross-experiment honesty a standing property: any
  future study's trials join the union, and the bar only rises with more
  searching. New strategies cannot quietly "win" by being the best of many
  separately-reported searches.
- CSCV is `O(C(S, S/2))`; `S = 10` (252 splits) is the default — ample for a
  stable PBO and fast on the study sizes here.

## Validation evidence (committed tests)

- `tests/test_cscv.py::test_pure_noise_search_is_overfit` (PBO > 0.5 on noise)
- `tests/test_cscv.py::test_one_genuinely_superior_config_is_robust` (PBO < 0.1)
- `tests/test_cscv.py::test_deterministic_and_split_count`
- Reproduce everything: `make research` → `results/research-report.html`
