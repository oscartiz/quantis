# ADR-003: Regime-model selection — Gaussian HMM and BOCPD, together

- Status: accepted
- Date: 2026-06-12

## Context

The research layer needs at least two regime-switching models, compared
rigorously, each with a stated hypothesis (project brief). Regime models for
financial returns broadly split into two families:

1. **Recurring latent states.** A fixed, small set of regimes (e.g.
   bear/chop/bull) that the market revisits, with Markov transitions between
   them. Hidden Markov Models are the canonical instance.
2. **Piecewise segments.** The data-generating parameters are constant within
   a segment and change at *changepoints*; segments do not recur. Bayesian
   Online Changepoint Detection (BOCPD) is the canonical instance.

These are not two implementations of one idea — they encode genuinely
different beliefs about how markets change, and they differ on the axis this
whole project cares about most: **look-ahead**.

## Decision

Ship **both**, as `quantis.models.hmm.GaussianHMM` and
`quantis.models.bocpd.Bocpd`, and treat their contrast as a first-class
result rather than picking a "winner".

- **Gaussian HMM (3-state, diagonal covariance, own Baum-Welch EM).**
  Hypothesis: returns come from a few *persistent, recurring* regimes with
  constant transition probabilities. Smoothed state posteriors use the
  **whole sample** — `p(state_t | x_{1:T})`. This is a powerful *offline,
  non-causal* analysis lens.
- **BOCPD (Adams–MacKay, NIG prior, Student-t predictive).** Hypothesis:
  parameters are *piecewise constant* with a constant per-step hazard of a
  break. The run-length posterior at time `t` uses **only** `x_{1:t}`. This is
  *online and causal by construction*.

## Why both, not one

The pair is chosen so that one model is everything the other is not, along the
axes that matter:

| | Gaussian HMM | BOCPD |
|---|---|---|
| Regimes | fixed K, recurring | unbounded, non-recurring segments |
| Information set | full sample (smoothing) | causal (`x_{1:t}` only) |
| Output | state posterior + Viterbi path | run-length posterior |
| Natural use | labelling history for analysis | live regime/break signal |
| Look-ahead | **yes** (uses the future) | **no** |

This is the look-ahead lesson made concrete: the HMM's smoothed regime label
at time `t` is partly determined by data after `t`, so using it as a trading
signal would be silent look-ahead bias. BOCPD cannot leak the future even in
principle. A reviewer who internalizes only this table has learned the single
most important thing about backtesting regime strategies. The signal a live
strategy may consume is the **causal** one (BOCPD, or the HMM's *filtered*
— not smoothed — posterior); the smoothed HMM is for research and attribution.

## Alternatives considered

- **HMM vs. Markov-switching GARCH.** MS-GARCH is a strong volatility-regime
  model, but it lives in the same recurring-latent-state family as the HMM and
  is both causal-or-not by the same smoothing choice — so the *pair* would not
  isolate the look-ahead axis. It is a natural Phase-5+ addition, not the
  contrast we want first.
- **Two HMMs (different K).** Tests sensitivity to K, not a difference in
  modelling assumptions; pedagogically weak.
- **Wrapping `hmmlearn` instead of writing EM.** Rejected: implementing
  Baum-Welch ourselves is the portfolio signal, and it lets us validate
  against `hmmlearn` as an independent oracle (which we do, from a shared
  initialization — the only fair comparison for a non-convex objective). The
  library stays a dev-only test dependency and is never imported by shipped
  code.

## Consequences

- Two models to maintain and two hypotheses to test. Accepted: the comparison
  is the deliverable.
- The evaluation layer (Phase 3) must be careful to feed each model a
  **causal** feature set and to use the *causal* regime signal for any
  tradeable backtest — the HMM's smoothed labels are for attribution and
  plots only, and the dashboard must label them as such.
- Both models are validated: the HMM recovers known synthetic parameters and
  matches `hmmlearn`'s log-likelihood and means to ~1e-3/2e-4 from a shared
  start; BOCPD recovers known changepoints within a few samples and its
  causality is asserted by re-running on prefixes.

## Validation evidence (committed tests)

- `tests/test_hmm.py::test_matches_hmmlearn_from_shared_initialization`
- `tests/test_hmm.py::test_recovers_known_regime_means`
- `tests/test_bocpd.py::test_detects_changepoints_near_truth`
- `tests/test_bocpd.py::test_output_is_causal_online`
