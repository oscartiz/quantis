# ADR-010: Long/short regime trading — shorting the bear regime

- Status: accepted
- Date: 2026-06-17

## Context

Every shipped strategy is long/flat: long in a confirmed bull regime, cash
otherwise. The HMM also identifies a *bear* regime (the lowest-mean state), which
a long-only book can only sidestep, never exploit. Two facts make the symmetric
short worth a careful, bounded experiment:

- **Funding flips sign for a short.** A long perp pays funding when the rate is
  positive; a short *receives* it. BTC funding is positive most of the time, so
  shorting the bear regime carries a structural funding tailwind the long-only
  book never sees.
- **But shorting adds tail risk** a long/flat book does not have (unbounded loss
  on a squeeze). Whether the tailwind outweighs the tail is an empirical question,
  not an assumption.

## Decision

Add `quantis.evaluation.directional_strategy.causal_long_short_returns`: long the
bull regime (`+1`), short the bear regime (`-1`) when `short_bear` is set, flat in
chop. Funding is charged on the *signed* position — the existing
`position · funding_daily` term already credits a short for positive funding, so
the short side is priced correctly, not as a drag. Turnover cost is charged on
`|Δposition|` (a bull→bear flip costs two legs). With `short_bear=False` it
reduces **exactly** to `causal_regime_returns` (tested), so it is a strict
generalization, and it uses only the filtered (causal) regime — no look-ahead.

`scripts/short_eval.py` evaluates it the same way as every other study: a config
search (vol_window × n_states), OOS-within-research, net of real funding, with
Deflated Sharpe, SPA (vs cash and vs the long/flat book), and PBO.

## Result (reported as-is)

Shorting the bear regime **does not pay** on this history, even with the funding
tailwind:

| measure | value | reading |
|---|---|---|
| configs where shorting beats long/flat (raw) | 3/9 | usually it *hurts* |
| best long/short Sharpe (ann.) | +0.86 (`ls_k2_vol15`) | but its long/flat twin is +1.02 |
| Deflated Sharpe (9 configs) | 0.551 | not significant |
| SPA p-value (best beats cash) | 0.469 | absolute edge not significant |
| SPA p-value (best beats long/flat) | 0.830 | the short leg does **not** beat long/flat |
| **PBO (CSCV)** | **0.774** | the short-leg search is strongly overfit |

Walk-forward (research only, net funding) confirms it: adding the short leg
*lowers* the OOS distribution — pooled Sharpe **0.25 → 0.03**, median window
Sharpe **+0.13 → −0.11**. The funding a short collects in a bear regime does not
compensate for the bear-regime price risk plus the model's late/false bear calls;
the high PBO says the occasional config where shorting "helps" is selection
luck, not signal.

**Verdict: keep the default book long/flat.** The short leg is shipped as a
tested, correctly-funded capability and an honest negative result — useful to
have measured, not useful to deploy.

## Alternatives considered

- **Always-on long/short as the new default.** Rejected — the evidence says it
  degrades risk-adjusted returns and adds tail risk; making it default would
  contradict the data. It is opt-in (`short_bear=True`).
- **Conviction- or vol-scaled shorts.** The short leg first has to clear the
  basic question "does shorting the bear help at all?"; it does not, so layering
  sizing on top would be polishing a negative result. (Sizing lives in ADR-008
  and could be composed later if the base case ever turned positive.)
- **Shorting chop too / a fully-invested long/short book.** Rejected: the chop
  regime has no directional thesis, so shorting it is noise trading. Only the
  bear regime carries a (testable) short thesis.

## Consequences

- The strategy space now spans long/flat and long/short through one causal
  function and the same harness; the walk-forward harness already accepts it via
  `returns_fn`.
- A documented negative result with a high PBO is itself a deliverable: it shows
  the machinery rejecting a plausible idea ("shorts collect funding!") on the
  evidence, which is the posture the whole project is built to demonstrate.
- The honest downside: tested but not deployed. No part of the default path
  shorts; enabling it is an explicit, evidence-contradicting choice.

## Validation evidence (committed tests)

- `tests/test_directional.py::test_no_short_recovers_long_flat`
- `tests/test_directional.py::test_shorting_adds_short_positions_only`
- `tests/test_directional.py::test_funding_helps_shorts_and_hurts_longs`
- `tests/test_directional.py::test_prefix_causal`
- Reproduce the study: `uv run --project python python python/scripts/short_eval.py`
