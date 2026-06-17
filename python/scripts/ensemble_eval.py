"""Does the BOCPD risk-off overlay actually improve the HMM regime filter?

The repo ships two regime models but, until now, only the HMM drove a strategy
(ADR-003). ``quantis.evaluation.ensemble_strategy`` puts BOCPD to work as a
one-directional risk-off overlay (ADR-007): while a fresh segment has not
persisted, force the position flat even if the HMM still reads bull. This script
asks — honestly, with the repo's own machinery — whether that helps.

Design (no look-ahead, net of real funding), mirroring ``regime_search.py``:

* Fit the HMM once on the first half of the **research** partition (the sealed
  holdout is never touched here) and causally evaluate on the second half.
* Sweep the overlay's two knobs — hazard (expected segment length) and the
  ``min_run_length`` confirmation bars — a small, genuine grid.
* Report each variant against the plain HMM baseline, then correct for the
  search:
    - Deflated Sharpe deflates the best overlay's Sharpe by the expected maximum
      across the whole overlay grid.
    - SPA / Reality Check bootstrap-test whether the best overlay beats *cash*
      and whether it beats the *plain HMM* (the benchmark that matters here).
* Finally, walk the plain HMM and the best overlay through the identical
  walk-forward harness (research only) to compare their out-of-sample
  *distributions*, not a single split.

The verdict is printed as-is. "The overlay trims drawdown and exposure but adds
no edge the deflation can't explain" is a valid, honest outcome.

Run (from repo root):
    uv run --project python python python/scripts/ensemble_eval.py
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
from quantis.evaluation.ensemble_strategy import causal_ensemble_returns
from quantis.evaluation.metrics import (
    deflated_sharpe_ratio,
    max_drawdown,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)
from quantis.evaluation.multiple_testing import spa_test
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm
from quantis.evaluation.trial_log import TrialLog, TrialRecord
from quantis.evaluation.walk_forward import WalkForwardResult, walk_forward_evaluate

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 365
_VOL_WINDOW = 20
_HAZARDS = [60.0, 100.0, 150.0]
_MIN_RUN_LENGTHS = [3, 5, 8]
# Walk-forward sweep (research only; coarse enough to refit HMM+BOCPD per window).
_WF = {"train_min": 400, "test_window": 90, "step": 45}


@dataclass(frozen=True)
class Variant:
    hazard: float
    min_run_length: int

    @property
    def name(self) -> str:
        return f"ovl_h{int(self.hazard)}_m{self.min_run_length}"


def _stats(strat: Array, position: Array) -> tuple[float, float, float, float]:
    """(annualized Sharpe, time in market, max drawdown, total return)."""
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

    # One HMM, fit on the first half of research; reused by every variant.
    model = fit_regime_hmm(close[:train_end], seed=42, vol_window=_VOL_WINDOW)

    def oos(returns):  # type: ignore[no-untyped-def]
        gidx = slice_start + returns.candle_index
        mask = gidx >= train_end
        return returns.strat[mask], returns.position[mask], gidx[mask]

    hmm_strat, hmm_pos, gidx = oos(
        causal_regime_returns(model, sliced, vol_window=_VOL_WINDOW, funding_daily=f_sliced)
    )
    hmm_sharpe, hmm_inmkt, hmm_dd, hmm_total = _stats(hmm_strat, hmm_pos)
    n_obs = hmm_strat.size

    variants = [Variant(h, m) for h in _HAZARDS for m in _MIN_RUN_LENGTHS]
    log_path = _REPO_ROOT / "results" / "ensemble-trial-log.jsonl"
    log_path.unlink(missing_ok=True)
    log = TrialLog(log_path)

    rows = []
    strat_cols = []
    excess_vs_hmm = []
    for v in variants:
        strat, pos, vidx = oos(
            causal_ensemble_returns(
                model,
                sliced,
                vol_window=_VOL_WINDOW,
                funding_daily=f_sliced,
                hazard_lambda=v.hazard,
                min_run_length=v.min_run_length,
            )
        )
        assert np.array_equal(vidx, gidx)  # identical OOS rows -> directly comparable
        log.append(TrialRecord(name=v.name, returns=[float(x) for x in strat]))
        strat_cols.append(strat)
        excess_vs_hmm.append(strat - hmm_strat)
        rows.append((v, *_stats(strat, pos)))

    rows.sort(key=lambda r: r[1], reverse=True)  # by Sharpe

    print(
        f"overlay sweep: {len(variants)} variants, OOS-within-research "
        f"({n_obs} aligned days, net of funding)\n"
    )
    print(f"  {'variant':16}{'Sharpe':>9}{'in-mkt':>9}{'maxDD':>8}{'totRet':>9}{'vs HMM':>9}")
    print(
        f"  {'hmm_baseline':16}{hmm_sharpe:>+9.2f}{hmm_inmkt * 100:>8.0f}%"
        f"{hmm_dd * 100:>7.1f}%{hmm_total * 100:>+8.1f}%{'—':>9}"
    )
    for v, sh, inmkt, dd, total in rows:
        beats = "yes" if sh > hmm_sharpe else "no"
        print(
            f"  {v.name:16}{sh:>+9.2f}{inmkt * 100:>8.0f}%"
            f"{dd * 100:>7.1f}%{total * 100:>+8.1f}%{beats:>9}"
        )

    # --- multiple-testing correction over the overlay search ---
    best = rows[0]
    best_v, best_sharpe = best[0], best[1]
    best_strat = strat_cols[variants.index(best_v)]
    trial_sharpes = log.sharpes()
    psr = probabilistic_sharpe_ratio(best_strat, 0.0, 1.0)
    dsr = deflated_sharpe_ratio(best_strat, trial_sharpes, 1.0)
    spa_cash = spa_test(np.column_stack(strat_cols), seed=42)  # beats cash (0)
    spa_hmm = spa_test(np.column_stack(excess_vs_hmm), seed=42)  # beats the plain HMM

    n_beat = sum(1 for _, sh, *_ in rows if sh > hmm_sharpe)
    print("\n=== MULTIPLE-TESTING VERDICT (net of funding, OOS-within-research) ===")
    print(f"  overlay variants beating plain HMM on Sharpe (raw): {n_beat}/{len(variants)}")
    print(
        f"  best overlay: {best_v.name}  (Sharpe ann {best_sharpe:+.2f} vs HMM {hmm_sharpe:+.2f})"
    )
    print("  -- absolute edge (is the best overlay's Sharpe real after the search?) --")
    print(f"  PSR (vs 0, uncorrected):          {psr:.3f}")
    print(f"  Deflated Sharpe ({len(variants)} variants):  {dsr:.3f}")
    print(f"  SPA p-value (best beats cash):    {spa_cash.spa_pvalue:.3f}")
    print("  -- relative (does the overlay beat the plain HMM it overlays?) --")
    print(f"  SPA p-value (best beats HMM):     {spa_hmm.spa_pvalue:.3f}")
    print(f"  Reality Check p-value (conserv.): {spa_hmm.reality_check_pvalue:.3f}")
    survives = dsr > 0.95 and spa_hmm.spa_pvalue < 0.05
    verdict = "overlay edge survives the search" if survives else "no edge survives the correction"
    print(f"  --> {verdict}")

    # --- walk-forward distribution: plain HMM vs the best overlay (research only) ---
    research = close[:boundary]
    research_funding = funding[:boundary]
    overlay_fn = functools.partial(
        causal_ensemble_returns,
        hazard_lambda=best_v.hazard,
        min_run_length=best_v.min_run_length,
    )

    def wf(returns_fn) -> WalkForwardResult:  # type: ignore[no-untyped-def]
        return walk_forward_evaluate(
            research, **_WF, seed=42, funding_daily=research_funding, returns_fn=returns_fn
        )

    wf_hmm = wf(causal_regime_returns)
    wf_ovl = wf(overlay_fn)
    print(
        f"\n=== WALK-FORWARD DISTRIBUTION "
        f"(research only, {wf_hmm.n_windows} windows, net funding) ==="
    )
    print(f"  {'metric':24}{'plain HMM':>12}{'best overlay':>14}")
    for label, a, b in (
        ("pooled Sharpe", wf_hmm.pooled_strat_sharpe, wf_ovl.pooled_strat_sharpe),
        ("median window Sharpe", wf_hmm.median_strat_sharpe, wf_ovl.median_strat_sharpe),
        ("frac windows positive", wf_hmm.frac_positive_return, wf_ovl.frac_positive_return),
        ("mean time in market", wf_hmm.mean_time_in_market, wf_ovl.mean_time_in_market),
    ):
        print(f"  {label:24}{a:>+12.2f}{b:>+14.2f}")
    mean_dd_hmm = float(np.mean([w.strat_max_drawdown for w in wf_hmm.windows]))
    mean_dd_ovl = float(np.mean([w.strat_max_drawdown for w in wf_ovl.windows]))
    print(f"  {'mean window max-DD':24}{mean_dd_hmm:>+12.2f}{mean_dd_ovl:>+14.2f}")

    artifact = {
        "n_variants": len(variants),
        "n_obs": n_obs,
        "eval": "OOS-within-research (fit first half, causal second half, net of funding)",
        "hmm_baseline": {
            "sharpe_ann": hmm_sharpe,
            "time_in_market": hmm_inmkt,
            "max_drawdown": hmm_dd,
            "total_return": hmm_total,
        },
        "best_overlay": best_v.name,
        "best_overlay_sharpe_ann": best_sharpe,
        "variants_beating_hmm": n_beat,
        "psr_uncorrected": psr,
        "deflated_sharpe": dsr,
        "spa_pvalue_vs_cash": spa_cash.spa_pvalue,
        "spa_pvalue_vs_hmm": spa_hmm.spa_pvalue,
        "reality_check_pvalue_vs_hmm": spa_hmm.reality_check_pvalue,
        "edge_survives_correction": survives,
        "walk_forward": {
            "n_windows": wf_hmm.n_windows,
            "pooled_sharpe_hmm": wf_hmm.pooled_strat_sharpe,
            "pooled_sharpe_overlay": wf_ovl.pooled_strat_sharpe,
            "mean_time_in_market_hmm": wf_hmm.mean_time_in_market,
            "mean_time_in_market_overlay": wf_ovl.mean_time_in_market,
            "mean_window_max_dd_hmm": mean_dd_hmm,
            "mean_window_max_dd_overlay": mean_dd_ovl,
        },
    }
    out = _REPO_ROOT / "results" / "ensemble-eval.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\n  artifacts: {out}  and  {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
