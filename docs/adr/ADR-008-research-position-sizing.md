# ADR-008: Position sizing in the research layer — vol targeting + conviction

- Status: accepted
- Date: 2026-06-16

## Context

The `risk` crate has always known how to *size* a position — volatility
targeting and capped fractional Kelly (ADR-005) — and the execution path uses
it. But the **research** layer never did: the HMM filter, its BOCPD overlay
(ADR-007), the walk-forward harness, the holdout, and every search traded a
**binary** long/flat book (hold one unit in a bull regime, else cash).

That binary rule discards two things the model already produces:

1. **current volatility** — a fixed unit position carries far more risk in a
   6%/day tape than a 1%/day one; and
2. **regime confidence** — the filtered posterior `P(bull)` is a probability, yet
   the binary `argmax` rule throws away everything strictly between 0 and 1.

A drawdown-avoider that ignores how risky the market is right now, and how sure
it is of the regime, is leaving obvious risk-shaping on the table. The question:
does sizing the position — faithfully to the risk crate's own sizer — improve the
strategy, and does any improvement survive the repo's multiple-testing bar?

## Decision

Port the risk crate's volatility-target sizer into the causal research path as
`quantis.evaluation.sizing_strategy.causal_sized_returns`, replacing the 0/1
position with an **equity weight**:

- **Volatility targeting** — `w = clamp(target_vol / realized_vol, 0,
  max_leverage)`, exactly `crate::risk::vol_target_qty` expressed as a fraction of
  equity (`notional = equity · target_vol/realized_vol`, capped at
  `max_leverage · equity`). Hold less when the tape is wild, more when calm,
  never above the leverage cap even as `realized_vol → 0`.
- **Conviction weighting** (optional) — scale by `P(bull)` (the filtered
  posterior) instead of the hard `argmax` regime, so a 0.9-confidence bull is
  held larger than a 0.55 one.

The weight is `signal · size`; costs are charged on **turnover** `|Δw|` (the
continuous generalization of the binary round-trip) and funding accrues on the
held weight. With `target_vol=None` and the hard signal it reproduces the binary
strategy **exactly** (tested), so this is a strict generalization, and the
integer `RiskGate` remains the sole live authority — this is advisory sizing
*research*, by the same advisory/authority split ADR-005 is built on. Causality
holds because `realized_vol` and `P(bull)` at row `t` use only data through `t`
(prefix-invariance re-asserted in `tests/test_sizing.py`).

## Result (reported as-is)

`scripts/sizing_eval.py`, OOS-within-research, **net of real funding**, sweeping
target_vol ∈ {0.015, 0.02, 0.03} · max_leverage ∈ {2, 3} · {hard, conviction} —
**12 variants**. Sharpe is the fair comparator (leverage scales return and
drawdown but not Sharpe):

| | binary | best sizing (`tv0.03_l2_cv`) |
|---|---:|---:|
| Sharpe (ann.) | +0.32 | **+0.49** |
| mean exposure | 0.21x | 0.19x |
| max drawdown | 20.9% | 17.0% |

Two findings stand out. First, the lift comes from **conviction weighting, not
vol targeting alone**: every conviction variant lands at Sharpe +0.49 while every
hard-regime variant sits at +0.28 (slightly *below* binary). All conviction
variants tie because Sharpe is scale-invariant and the leverage cap rarely binds,
so the weight is effectively `P(bull) · target_vol/realized_vol` up to a scalar.

Second, the project's own correction over the 12-variant search refuses to call
it an edge:

| measure | value | reading |
|---|---|---|
| PSR vs 0 (uncorrected) | 0.723 | unconvincing even before deflation |
| **Deflated Sharpe (12 variants)** | **0.645** | the lift is within search luck |
| SPA p-value (best beats cash) | 0.256 | absolute edge not significant |
| SPA p-value (best beats binary) | 0.246 | sizing does not beat the binary book |

Walk-forward (research only, 14 windows, net funding) tells the most interesting
part honestly: conviction sizing markedly improves OOS **consistency** —
median window Sharpe **0.13 → 0.70**, fraction of positive windows **50% → 79%**,
at slightly *lower* mean exposure (0.26 → 0.23) — while pooled Sharpe moves only
0.25 → 0.31 (the pool is dominated by a few large windows). So sizing buys
steadier per-window risk-adjusted returns, which is real and valuable, but **no
edge survives the single-split multiple-testing correction**. Consistent with the
rest of the repo: a risk-shaping improvement, not a searchable alpha.

## Alternatives considered

- **Capped fractional Kelly sizing instead of vol targeting.** The crate ships it
  too, but Kelly needs an `edge` estimate (expected per-bar return), which on
  daily BTC regimes is far noisier than `realized_vol`; vol targeting is the
  honest, low-parameter sizer to demonstrate first. Kelly is a natural follow-up.
- **Sizing as the live authority.** Rejected — ADR-005's whole point is that
  sizing is advisory and the integer gate is authority. Research sizing changes a
  *weight in a returns series*, never an order that reaches a venue.
- **Reporting the best variant without deflation.** Rejected on principle; the
  grid is searched and then deflated, and the winner is shown next to its DSR.
- **Annualized vol target as the knob.** Kept the target in the same per-bar
  units as `realized_vol` (the crate's contract: "same units"), avoiding a hidden
  annualization constant.

## Consequences

- The risk crate's sizer is now exercised in research, not only execution — the
  advisory/authority split spans both layers with one formula.
- The walk-forward harness's `position` field now carries a *weight*, so
  `mean(position)` reads as mean exposure (it coincides with time-in-market for a
  binary run); scripts label it accordingly.
- More knobs (target_vol, leverage, conviction) means more ways to overfit;
  mitigated by always reporting the sizing DSR/SPA over the search and by the
  `target_vol=None` baseline that provably recovers the binary book (tested).
- Honest downside unchanged: sizing reshapes risk and improves OOS consistency
  but is not demonstrated alpha. Shipped as capability + disciplined evaluation,
  the same posture as the rest of the project.

## Validation evidence (committed tests)

- `tests/test_sizing.py::test_baseline_equivalence_to_binary_strategy`
- `tests/test_sizing.py::test_vol_target_weight_matches_the_risk_crate_formula`
- `tests/test_sizing.py::test_conviction_weighting_uses_the_posterior`
- `tests/test_sizing.py::test_leverage_cap_binds_when_target_is_large`
- `tests/test_sizing.py::test_position_is_prefix_causal`
- Reproduce the study: `uv run --project python python python/scripts/sizing_eval.py`
