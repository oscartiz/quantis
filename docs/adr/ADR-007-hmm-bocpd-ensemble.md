# ADR-007: HMM + BOCPD ensemble — BOCPD as a one-directional risk-off overlay

- Status: accepted
- Date: 2026-06-16

## Context

ADR-003 shipped two regime models — a Gaussian HMM and BOCPD — and framed their
contrast (recurring states vs. piecewise segments; non-causal smoothing vs.
causal-by-construction) as a first-class result. But only the HMM ever drove a
strategy: every backtest, the walk-forward harness, the sealed holdout, and the
`regime_search` multiple-testing study used the HMM's filtered bull signal.
BOCPD was validated (it recovers known changepoints, its causality is asserted on
prefixes) and then sat unused as a *signal*.

That left value on the table and a question unanswered. The HMM filter is a
slow, smooth *directional* classifier: it is structurally late to leave a bull
regime once one breaks down, because a filtered posterior migrates state by
state. BOCPD detects, online and causally, that the return distribution just
broke — faster than the HMM re-classifies — but it cannot name the new regime's
direction. The natural question for a project whose demonstrated edge is
*drawdown avoidance* (README): can BOCPD make the exit faster without
introducing look-ahead, and does that actually help once you correct for trying?

## Decision

Compose the two as an **ensemble where BOCPD is a one-directional risk-off
overlay** on the HMM-bull strategy
(`quantis.evaluation.ensemble_strategy.causal_ensemble_returns`), and put the
combination through the *same* honesty harness as the HMM (walk-forward +
Deflated-Sharpe / SPA, net of real funding) rather than asserting it is better.

The composition respects what each model can honestly say:

- the **HMM** decides *direction* — go long only in a confirmed bull regime;
- **BOCPD** decides *stability* — it can only say "the distribution just
  changed", so it is used **strictly to subtract exposure, never to add it**:
  while a fresh segment has not persisted (MAP run length `< min_run_length`) the
  overlay forces the position flat even if the HMM still reads bull.

Because the gate can only turn long → flat, the ensemble position is `<=` the
HMM position on every bar (tested), so the overlay can only ever de-risk.

Causality is preserved end to end. BOCPD sees vol-standardized 1-step log
returns, where the standardizing volatility is measured over *strictly prior*
bars (a one-bar shift), so the scale of `r_t` never uses `r_t`. BOCPD's
run-length posterior at `t` uses only `x[:t+1]`, and the whole overlay's
prefix-invariance is re-asserted in `tests/test_ensemble.py`. The harness change
that lets *any* causal strategy be walked forward (`walk_forward_evaluate(...,
returns_fn=)`) defaults to the HMM, so committed numbers are unchanged.

## Result (reported as-is)

`scripts/ensemble_eval.py`, OOS-within-research (fit first half, causal second
half, **net of real funding**), sweeping the overlay's two knobs — hazard
∈ {60, 100, 150}, `min_run_length` ∈ {3, 5, 8}, **9 variants**:

| | plain HMM | best overlay (`h150_m8`) |
|---|---:|---:|
| Sharpe (ann.) | +0.32 | **+0.53** |
| max drawdown | 20.9% | **13.7%** |
| time in market | 21% | 20% |
| total return | +11.4% | +17.9% |

On the single split the overlay looks like a clear win — but the project's own
multiple-testing correction over the 9-variant search says otherwise:

| measure | value | reading |
|---|---|---|
| PSR vs 0 (uncorrected) | 0.738 | unconvincing even before deflation |
| **Deflated Sharpe (9 variants)** | **0.682** | the apparent lift is within search luck |
| SPA p-value (best beats cash) | 0.268 | absolute edge not significant |
| SPA p-value (best beats plain HMM) | 0.356 | the overlay does not beat the HMM it overlays |

Walk-forward (research only, 14 windows, net funding) shows the overlay does what
it is *designed* to do — small, consistent risk reduction, not new alpha: pooled
Sharpe 0.25 → 0.31, mean window max-drawdown 0.09 → 0.08, mean time in market
0.26 → 0.24.

**Verdict: no edge survives the correction.** The overlay trims drawdown and
exposure, exactly the mechanical effect it is built for, but adds no statistically
defensible alpha over the plain HMM once you account for trying nine settings.
That is a valid, honest outcome and it is consistent with every other finding in
the repo (the edge is *episodic and regime-specific*, not *general and
searchable*).

## Alternatives considered

- **BOCPD as a standalone directional signal.** Rejected: BOCPD detects *that* a
  break occurred, not its direction, so a changepoint cannot say "go long". Using
  it directionally would be inventing information the model does not produce.
- **A symmetric agreement ensemble (long only if HMM-bull *and* BOCPD-stable;
  short/flat otherwise).** The de-risk-only formulation is a special case that
  keeps the strict invariant "the overlay can only reduce exposure", which makes
  the comparison to the HMM clean (every difference is a removed long) and avoids
  smuggling a short book into a long/flat study.
- **Tuning the overlay to the single split and reporting the winner.** Rejected
  on principle — that is precisely the over-fitting the repo exists to expose.
  The grid is searched and then *deflated*; the winner is reported next to its
  DSR, not instead of it.
- **Threading a full-series BOCPD run-length through the walk-forward** (BOCPD is
  prefix-invariant, so this is also causal). Not needed: recomputing BOCPD per
  window on each slice is simpler, equally causal, and only mildly conservative
  at window starts; both arms see the same slices.

## Consequences

- BOCPD now earns its place as a *signal*, not just a validated model — the
  ADR-003 pair finally meets in one strategy.
- The walk-forward harness is now strategy-generic (`returns_fn=`), so future
  causal strategies get the N→distribution treatment for free.
- A new tunable surface (hazard, `min_run_length`) means more ways to overfit;
  this is mitigated by always reporting the overlay's DSR/SPA over the search,
  never a hand-picked variant. `min_run_length=0` provably collapses the overlay
  back to the plain HMM (tested), so the baseline is never lost.
- The honest downside stands: the overlay is a risk-shaping tool with no proven
  edge. It is shipped as a *capability and a demonstration of disciplined
  evaluation*, not as alpha — the same posture as the rest of the project.

## Validation evidence (committed tests)

- `tests/test_ensemble.py::test_overlay_only_de_risks`
- `tests/test_ensemble.py::test_min_run_length_zero_recovers_the_hmm`
- `tests/test_ensemble.py::test_run_length_is_prefix_causal`
- `tests/test_ensemble.py::test_ensemble_position_is_prefix_causal`
- `tests/test_ensemble.py::test_run_length_resets_on_a_volatility_break`
- Reproduce the study: `uv run --project python python python/scripts/ensemble_eval.py`
