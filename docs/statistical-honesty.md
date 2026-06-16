# Statistical honesty

This document is the reviewer-facing summary of how Quantis avoids fooling
itself — and the one-shot holdout result, reported as-is. It consolidates
mechanisms that live in code and tests; every claim here is backed by something
runnable, cited inline.

The order matters: each defence below blocks a specific, well-known way that a
backtest produces a number that does not survive contact with reality.

## 1. Look-ahead bias — features cannot see the future

Every feature is causal by construction (NaN warmup, no forward fill), and the
guarantee is *tested*, not asserted. The leakage check
(`quantis.features.is_causal`) uses an **expanding-window-endpoint** test: a
causal feature computed on `series[:t+1]` must reproduce the full-series value
at `t`. A forward-peeking feature cannot, and the canary test registers two
deliberately leaky features (a future return and a centered mean) and proves the
check catches both while the five real features pass.

- Code: `python/quantis/features/pipeline.py`
- Test: `tests/test_features.py::test_canary_flags_forward_peeking_feature`

The same principle is enforced at the *model* level: the HMM's smoothed
posterior is non-causal and is used only for analysis/plotting; the **filtered**
posterior (`filter_proba`) is the causal signal used for any tradeable curve,
and its causality is tested by prefix re-runs (ADR-003).

## 2. Leakage at fold boundaries — purged, embargoed CV

Ordinary k-fold leaks on serially-correlated data with multi-bar labels.
`quantis.evaluation` provides walk-forward (expanding, strictly causal) and
**purged k-fold** splits: training samples whose label window overlaps the test
fold are purged, and an embargo drops a post-fold buffer. `assert_no_leakage`
is the CV-level canary — a test proves it catches a naive unpurged k-fold and
passes the purged one.

- Code: `python/quantis/evaluation/cross_validation.py`
- Test: `tests/test_cross_validation.py::test_leakage_guard_catches_unpurged_kfold`

## 3. Multiple testing — Deflated Sharpe and SPA

Search enough strategies and the best one's Sharpe is inflated by luck. Two
corrections, computed over a **logged** trial history (not a guessed count):

- **Deflated Sharpe Ratio** — the probabilistic Sharpe with the benchmark set
  to the expected maximum Sharpe across the trials searched. Demonstrated: the
  best of 200 zero-edge strategies has a naive PSR > 0.9 but a **DSR < 0.7**.
- **Hansen's SPA / White's Reality Check** — bootstrap tests of whether the best
  strategy beats the benchmark given the whole set. Demonstrated: ignores pure
  noise yet detects an injected edge, and SPA is never less powerful than RC.

- Code: `python/quantis/evaluation/{metrics,multiple_testing,trial_log}.py`
- Tests: `tests/test_evaluation_stats.py`

### Applied to the real strategy (not just the synthetic demo)

These corrections are only worth shipping if they are turned on the actual
strategy, where the temptation to over-claim lives.
`scripts/regime_search.py` does exactly that: it searches **18 regime-model
configurations** (volatility window × number of states — the genuine knobs),
evaluates each **out-of-sample within the research partition** (fit on the first
half, causally evaluate on the second, **net of real funding**), logs every
trial, and corrects for the search:

| measure | value | reading |
|---|---|---|
| best config Sharpe (ann.) | +1.17 | tempting in isolation |
| PSR vs 0 (uncorrected) | 0.94 | looks borderline-significant |
| **Deflated Sharpe (18-config search)** | **0.61** | not convincing once you correct for the search |
| SPA p-value (best beats cash) | 0.44 | absolute edge not significant |
| SPA p-value (best beats buy & hold) | 1.00 | rejected on this bull-dominated OOS span |

The verdict is **no edge survives the correction**, and that is the honest
result: an uncorrected PSR of 0.94 would have invited a significance claim that
the deflation (DSR 0.61) shows is an artifact of trying 18 things. Two caveats
stated plainly: the SPA-vs-buy-&-hold figure is *regime-dependent* — this OOS
split lands on a bull span where a long-only-in-bull risk filter is built to
underperform, so 1.00 is expected, not informative; and DSR is Sharpe-based
while SPA is mean-based, so the best config beats buy-and-hold on Sharpe (lower
volatility) yet not on mean return — the "drawdown-avoider, not return-maximizer"
finding, quantified. None of this contradicts the sealed holdout: the edge is
*episodic and regime-specific* (it appeared in a bear window), not a *general,
searchable* alpha — and the project's own machinery says so.

- Reproduce: `uv run --project python python python/scripts/regime_search.py`

## 4. Survivorship and instrument choice

Stated plainly because it cannot be fully fixed: choosing BTC because it is
liquid *today* is itself a survivorship decision. The method must generalize to
instruments selected ex-ante; the multi-asset event model and risk layer are
built for that, but the demonstrated results are single-asset and should be read
as such. The bundled candle data's earliest span has zero exchange volume
(backfilled OHLC), documented in `data/sample/PROVENANCE.md`, and volume-based
features are not used there.

## 5. Out-of-sample discipline — the holdout, evaluated once

The last 20% of the candle history was sealed **before** any holdout
evaluation: the manifest (boundary + content hash) was committed
(`data/sample/holdout-manifest.json`) in its own commit. `load_research` refuses
to return anything past the boundary; `reveal_holdout` is gated on an explicit
acknowledgement and re-verifies the hash. The model was fit on the research
partition only, and the strategy evaluated on the holdout **exactly once**.

### The result (reported as-is)

Holdout span: **2025-10-05 → 2026-06-13** (231 evaluated days).

| metric | causal regime strategy | buy & hold |
|---|---|---|
| total return | **+19.9%** | −42.7% |
| Sharpe (ann.) | **+1.40** | −1.84 |
| Sortino (ann.) | +3.32 | — |
| max drawdown | **9.5%** | 50.6% |
| time in market | 13% | 100% |

Reproduce: `uv run --project python python python/scripts/evaluate_holdout.py`.

**Net of real funding** (a long-only perp pays funding for every day it holds —
modelled with the hash-pinned `data/sample/btc-funding.csv`, real Hyperliquid
rates), the holdout is essentially unchanged: **+19.8%, Sharpe +1.39**. The drag
is negligible here because the strategy held long only ~30 days, all during
risk-off bounces where funding was cheap (+0.003%/day vs the +0.040%/day
all-history mean — a long-only-in-bull filter is structurally long when funding
is low). Reproduce: `scripts/evaluate_funding_impact.py`.

### How to read it honestly

This is a **good out-of-sample number, and it is also N = 1 in the strategy's
favourable environment.** The holdout window happened to be a *bear market*, and
the strategy is a long-only-in-bull-regime risk filter — its entire design is to
go to cash when it does not see a bull regime. So "avoided a −43% drawdown and
ended +20% at 13% exposure" is the strategy doing exactly what it is built to do
in exactly the conditions built for. It is **not** evidence of a robust,
all-weather edge:

- On the *in-sample* span (2023–2025, bull-dominated), the same strategy
  **underperformed** buy-and-hold on Sharpe (0.32 vs 0.65) — it gave up upside.
  See the dashboard.
- One 8-month holdout, however favourable, cannot establish an edge. A single
  draw from a distribution is a single draw.

The honest conclusion: this is a **risk-reducing regime filter** with a strong
showing in one bear holdout and a weak showing in a bull in-sample period — i.e.
it trades return for drawdown protection, period-dependently. That is a finding
worth reporting truthfully, not an alpha to advertise. The credibility is in the
*discipline that produced the number* (sealed, hashed, evaluated once), not in
the number being large.

### The distribution behind the holdout (walk-forward)

A single out-of-sample window — even a sealed one — is one draw. To turn N = 1
into a distribution, the walk-forward harness refits the model on all data up to
each point and evaluates the next ~quarter, walling off only model *fitting* to
the past (`quantis.evaluation.walk_forward`,
`scripts/walk_forward_eval.py`). Across **20 quarterly out-of-sample windows**:

| measure | value |
|---|---|
| per-window strategy Sharpe | mean +0.56, **median 0.00**, std 1.12 |
| windows with positive return | **40%** |
| windows beating buy-and-hold Sharpe | 60% |
| mean time in market | 24% |
| pooled OOS (all windows concatenated) | strategy Sharpe **+0.60** vs hold +0.20; return +123% vs +58% |

**Net of real funding** the pooled edge shrinks materially — the walk-forward
holds 24% of the time, including through the 2024 bull where funding is expensive
(everyone is long), so the pooled return falls **+123% → +74.5%** and the pooled
Sharpe **0.60 → 0.42** (still ahead of buy-and-hold's 0.20, but the return margin
roughly halves). The per-window character is unchanged — median 0.00, 40%
positive — funding trims the magnitude, not the lumpiness. (`evaluate_funding_impact.py`.)

This is the complete picture, and it is more sobering than the single holdout.
The *pooled* out-of-sample result is genuinely decent — trading the strategy
across every window in turn beats buy-and-hold on both Sharpe (0.60 vs 0.20) and
total return (+123% vs +58%) over 2024–2026. But the *per-window distribution*
shows why that is not the whole story: the **median window Sharpe is zero** and
**only 40% of windows are positive**. The strategy earns its pooled result
*episodically* — in a few favourable (mostly risk-off) windows — and does
nothing in most. The sealed +19.9% holdout was one of the good windows, exactly
as "N = 1 in the strategy's favourable environment" warned. A practitioner
should read this as: *real but lumpy and regime-dependent edge, not a steady
one* — and size and expectations accordingly.

## 6. Transaction-cost and capacity reality

A number is also dishonest if it ignores what trading costs. `docs/losing-money.md`
quantifies fee sensitivity (fees ×1/×2/×3 → −2.82/−4.83/−6.84 on the L2 demo),
the latency-resolution ceiling, capacity mechanics, and regime instability. The
holdout figures above are net of a 5 bps round-trip cost; at 13% time in market
the cost drag is small, but a higher-turnover variant would erode it.

**Funding** is now modelled with real data, not assumed away: a long-only perp
pays funding for every day it holds, and the bundled `btc-funding.csv` (26,526
real Hyperliquid events, 8-hour early / hourly later — cadence taken from the
timestamps, never assumed) averages **+0.040%/day (~14.5%/yr)** with longs
paying on **87%** of days. Netting it in leaves the low-exposure holdout almost
untouched (+19.9% → +19.8%) but cuts the higher-exposure walk-forward pooled
Sharpe from 0.60 to 0.42 — exactly the kind of cost a close-to-close backtest
silently omits. `scripts/evaluate_funding_impact.py`.

## What none of this proves

That the strategy will make money live. These mechanisms prevent specific
self-deceptions; they do not manufacture edge. The most they establish is that
*if* there is an edge, this process will not have invented it — and that when
there is not, the process says so.
