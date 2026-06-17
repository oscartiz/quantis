"""Does volatility-targeted / conviction sizing beat the binary regime book?

The HMM filter (and its BOCPD overlay) trade a binary long/flat position. The
risk crate has always known how to size — vol targeting and capped Kelly,
ADR-005 — but the *research* never used it. ``quantis.evaluation.sizing_strategy``
ports the vol-target sizer into the causal research path; this script asks,
with the repo's own machinery, whether sizing the position actually helps.

Design (no look-ahead, net of real funding), mirroring ``regime_search.py`` and
``ensemble_eval.py``:

* Fit the HMM once on the first half of the **research** partition (the sealed
  holdout is never touched) and causally evaluate on the second half.
* Baseline = the binary long/flat strategy. Then sweep a small, genuine grid of
  sizing knobs: target volatility x leverage cap x {hard regime, conviction}.
* Report each variant against the binary baseline (Sharpe is the fair comparator
  — leverage scales return and drawdown but not Sharpe), then correct for the
  search with Deflated Sharpe and SPA (beats cash, and beats the binary book).
* Walk the binary baseline and the best sized variant through the identical
  walk-forward harness (research only) to compare their OOS *distributions*.

Run (from repo root):
    uv run --project python python python/scripts/sizing_eval.py
"""

from __future__ import annotations

import functools
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from quantis.data.candles import load_candles
from quantis.data.funding import load_daily_funding
from quantis.data.holdout import HoldoutManifest
from quantis.evaluation.metrics import (
    deflated_sharpe_ratio,
    max_drawdown,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)
from quantis.evaluation.multiple_testing import spa_test
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm
from quantis.evaluation.sizing_strategy import causal_sized_returns
from quantis.evaluation.trial_log import TrialLog, TrialRecord
from quantis.evaluation.walk_forward import WalkForwardResult, walk_forward_evaluate

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 365
_VOL_WINDOW = 20
_TARGET_VOLS = [0.015, 0.02, 0.03]
_MAX_LEVERAGES = [2.0, 3.0]
_CONVICTION = [False, True]
_WF = {"train_min": 400, "test_window": 90, "step": 45}


@dataclass(frozen=True)
class Variant:
    target_vol: float
    max_leverage: float
    conviction: bool

    @property
    def name(self) -> str:
        tag = "cv" if self.conviction else "hr"
        return f"tv{self.target_vol:.3f}_l{self.max_leverage:.0f}_{tag}"


def _stats(strat: Array, position: Array) -> tuple[float, float, float, float]:
    """(annualized Sharpe, mean exposure, max drawdown, total return)."""
    return (
        sharpe_ratio(strat, _TRADING_DAYS),
        float(np.mean(position)),
        max_drawdown(strat),
        float(np.exp(np.sum(strat)) - 1.0),
    )


def main() -> int:
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    manifest = HoldoutManifest.from_json(_REPO_ROOT / "data" / "sample" / "holdout-manifest.json")
    funding = load_daily_funding(
        _REPO_ROOT / "data" / "sample" / "btc-funding.csv", candles.dates_iso()
    )
    close = candles.close

    boundary = manifest.boundary_index  # research = close[:boundary]; holdout sealed
    train_end = boundary // 2
    slice_start = max(0, train_end - (_VOL_WINDOW + 5))
    sliced = close[slice_start:boundary]
    f_sliced = funding[slice_start:boundary]
    model = fit_regime_hmm(close[:train_end], seed=42, vol_window=_VOL_WINDOW)

    def oos(returns):  # type: ignore[no-untyped-def]
        gidx = slice_start + returns.candle_index
        mask = gidx >= train_end
        return returns.strat[mask], returns.position[mask], gidx[mask]

    base_strat, base_pos, gidx = oos(
        causal_regime_returns(model, sliced, vol_window=_VOL_WINDOW, funding_daily=f_sliced)
    )
    base_sharpe, base_exp, base_dd, base_total = _stats(base_strat, base_pos)
    n_obs = base_strat.size

    variants = [
        Variant(tv, lev, cv) for tv in _TARGET_VOLS for lev in _MAX_LEVERAGES for cv in _CONVICTION
    ]
    log_path = _REPO_ROOT / "results" / "sizing-trial-log.jsonl"
    log_path.unlink(missing_ok=True)
    log = TrialLog(log_path)

    rows = []
    strat_cols = []
    excess_vs_base = []
    for v in variants:
        strat, pos, vidx = oos(
            causal_sized_returns(
                model,
                sliced,
                vol_window=_VOL_WINDOW,
                funding_daily=f_sliced,
                target_vol=v.target_vol,
                max_leverage=v.max_leverage,
                conviction_weighted=v.conviction,
            )
        )
        assert np.array_equal(vidx, gidx)
        log.append(TrialRecord(name=v.name, returns=[float(x) for x in strat]))
        strat_cols.append(strat)
        excess_vs_base.append(strat - base_strat)
        rows.append((v, *_stats(strat, pos)))

    rows.sort(key=lambda r: r[1], reverse=True)  # by Sharpe

    print(
        f"sizing sweep: {len(variants)} variants, OOS-within-research "
        f"({n_obs} aligned days, net of funding)\n"
    )
    print(f"  {'variant':22}{'Sharpe':>9}{'exposure':>10}{'maxDD':>8}{'totRet':>9}{'vs base':>9}")
    print(
        f"  {'binary_baseline':22}{base_sharpe:>+9.2f}{base_exp:>9.2f}x"
        f"{base_dd * 100:>7.1f}%{base_total * 100:>+8.1f}%{'—':>9}"
    )
    for v, sh, exp, dd, total in rows:
        beats = "yes" if sh > base_sharpe else "no"
        print(
            f"  {v.name:22}{sh:>+9.2f}{exp:>9.2f}x{dd * 100:>7.1f}%{total * 100:>+8.1f}%{beats:>9}"
        )

    # --- multiple-testing correction over the sizing search ---
    best_v, best_sharpe = rows[0][0], rows[0][1]
    best_strat = strat_cols[variants.index(best_v)]
    trial_sharpes = log.sharpes()
    psr = probabilistic_sharpe_ratio(best_strat, 0.0, 1.0)
    dsr = deflated_sharpe_ratio(best_strat, trial_sharpes, 1.0)
    spa_cash = spa_test(np.column_stack(strat_cols), seed=42)
    spa_base = spa_test(np.column_stack(excess_vs_base), seed=42)

    n_beat = sum(1 for _, sh, *_ in rows if sh > base_sharpe)
    print("\n=== MULTIPLE-TESTING VERDICT (net of funding, OOS-within-research) ===")
    print(f"  sizing variants beating the binary book on Sharpe (raw): {n_beat}/{len(variants)}")
    print(
        f"  best sizing: {best_v.name}  "
        f"(Sharpe ann {best_sharpe:+.2f} vs binary {base_sharpe:+.2f})"
    )
    print("  -- absolute edge (is the best variant's Sharpe real after the search?) --")
    print(f"  PSR (vs 0, uncorrected):          {psr:.3f}")
    print(f"  Deflated Sharpe ({len(variants)} variants):  {dsr:.3f}")
    print(f"  SPA p-value (best beats cash):    {spa_cash.spa_pvalue:.3f}")
    print("  -- relative (does sizing beat the binary book it sizes?) --")
    print(f"  SPA p-value (best beats binary):  {spa_base.spa_pvalue:.3f}")
    print(f"  Reality Check p-value (conserv.): {spa_base.reality_check_pvalue:.3f}")
    survives = dsr > 0.95 and spa_base.spa_pvalue < 0.05
    verdict = "sizing edge survives the search" if survives else "no edge survives the correction"
    print(f"  --> {verdict}")

    # --- walk-forward distribution: binary baseline vs the best sized variant ---
    research = close[:boundary]
    research_funding = funding[:boundary]
    sized_fn = functools.partial(
        causal_sized_returns,
        target_vol=best_v.target_vol,
        max_leverage=best_v.max_leverage,
        conviction_weighted=best_v.conviction,
    )

    def wf(returns_fn) -> WalkForwardResult:  # type: ignore[no-untyped-def]
        return walk_forward_evaluate(
            research, **_WF, seed=42, funding_daily=research_funding, returns_fn=returns_fn
        )

    wf_base = wf(causal_regime_returns)
    wf_sized = wf(sized_fn)
    print(
        f"\n=== WALK-FORWARD DISTRIBUTION "
        f"(research only, {wf_base.n_windows} windows, net funding) ==="
    )
    print(f"  {'metric':24}{'binary':>12}{'best sizing':>14}")
    for label, a, b in (
        ("pooled Sharpe", wf_base.pooled_strat_sharpe, wf_sized.pooled_strat_sharpe),
        ("median window Sharpe", wf_base.median_strat_sharpe, wf_sized.median_strat_sharpe),
        ("frac windows positive", wf_base.frac_positive_return, wf_sized.frac_positive_return),
        ("mean exposure", wf_base.mean_time_in_market, wf_sized.mean_time_in_market),
    ):
        print(f"  {label:24}{a:>+12.2f}{b:>+14.2f}")

    artifact = {
        "n_variants": len(variants),
        "n_obs": n_obs,
        "eval": "OOS-within-research (fit first half, causal second half, net of funding)",
        "binary_baseline": {
            "sharpe_ann": base_sharpe,
            "mean_exposure": base_exp,
            "max_drawdown": base_dd,
            "total_return": base_total,
        },
        "best_sizing": best_v.name,
        "best_sizing_sharpe_ann": best_sharpe,
        "variants_beating_binary": n_beat,
        "psr_uncorrected": psr,
        "deflated_sharpe": dsr,
        "spa_pvalue_vs_cash": spa_cash.spa_pvalue,
        "spa_pvalue_vs_binary": spa_base.spa_pvalue,
        "reality_check_pvalue_vs_binary": spa_base.reality_check_pvalue,
        "edge_survives_correction": survives,
        "walk_forward": {
            "n_windows": wf_base.n_windows,
            "pooled_sharpe_binary": wf_base.pooled_strat_sharpe,
            "pooled_sharpe_sizing": wf_sized.pooled_strat_sharpe,
            "mean_exposure_binary": wf_base.mean_time_in_market,
            "mean_exposure_sizing": wf_sized.mean_time_in_market,
        },
    }
    out = _REPO_ROOT / "results" / "sizing-eval.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\n  artifacts: {out}  and  {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
