"""Does shorting the bear regime add value, net of funding and the search?

Every shipped strategy is long/flat. ``quantis.evaluation.directional_strategy``
adds the symmetric short leg (ADR-010): short the bear regime, long the bull,
flat in chop. A short *receives* funding when the rate is positive, so on
typically-positive-funding regimes the short leg has a real tailwind — but it
also adds tail risk. This script asks, with the repo's own machinery, whether
the short leg pays once funding and the multiple-testing correction are honest.

Design (no look-ahead, net of real funding), mirroring ``regime_search.py``:

* Search a grid of HMM configs (vol_window x n_states).
* For each config, fit on the first half of research and causally evaluate, on
  the second half, both the long/flat baseline and the long/short variant — same
  model, same bars, so the only difference is the short leg.
* Align every config to a common OOS date range, then correct over the long/short
  search with Deflated Sharpe, SPA (vs cash and vs the long/flat book), and PBO.
* Walk the long/flat and long/short books through the identical walk-forward
  harness for the default config to compare OOS distributions.

Run (from repo root):
    uv run --project python python python/scripts/short_eval.py
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
from quantis.evaluation.cscv import cscv_pbo
from quantis.evaluation.directional_strategy import causal_long_short_returns
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
_VOL_WINDOWS = [15, 20, 30]
_N_STATES = [2, 3, 4]
_WF = {"train_min": 400, "test_window": 90, "step": 45}


@dataclass(frozen=True)
class Config:
    vol_window: int
    n_states: int

    @property
    def name(self) -> str:
        return f"ls_k{self.n_states}_vol{self.vol_window}"


@dataclass(frozen=True)
class Evaluated:
    cfg: Config
    gidx: NDArray[np.int64]
    ls_strat: Array
    ls_pos: Array
    lf_strat: Array


def evaluate(
    cfg: Config, close: Array, funding: Array, *, train_end: int, boundary: int
) -> Evaluated:
    """Fit on close[:train_end]; causally evaluate long/short and long/flat OOS on
    (train_end, boundary), net of funding. Returns global-indexed OOS rows."""
    slice_start = max(0, train_end - (cfg.vol_window + 5))
    sliced = close[slice_start:boundary]
    f_sliced = funding[slice_start:boundary]
    model = fit_regime_hmm(
        close[:train_end], seed=42, vol_window=cfg.vol_window, n_states=cfg.n_states
    )
    ls = causal_long_short_returns(
        model,
        sliced,
        vol_window=cfg.vol_window,
        funding_daily=f_sliced,
        bull_rank=cfg.n_states - 1,
        bear_rank=0,
        short_bear=True,
    )
    lf = causal_regime_returns(
        model, sliced, vol_window=cfg.vol_window, funding_daily=f_sliced, bull_rank=cfg.n_states - 1
    )
    gidx = slice_start + ls.candle_index
    oos = gidx >= train_end
    return Evaluated(cfg, gidx[oos], ls.strat[oos], ls.position[oos], lf.strat[oos])


def main() -> int:
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    manifest = HoldoutManifest.from_json(_REPO_ROOT / "data" / "sample" / "holdout-manifest.json")
    funding = load_daily_funding(
        _REPO_ROOT / "data" / "sample" / "btc-funding.csv", candles.dates_iso()
    )
    close = candles.close
    boundary = manifest.boundary_index
    train_end = boundary // 2

    configs = [Config(vw, ns) for vw in _VOL_WINDOWS for ns in _N_STATES]
    evals = [evaluate(c, close, funding, train_end=train_end, boundary=boundary) for c in configs]

    # Align every config to the common OOS date range (warmup differs by vol_window).
    common = np.array(
        sorted(set.intersection(*[set(e.gidx.tolist()) for e in evals])), dtype=np.int64
    )

    def aligned(e: Evaluated, field: str) -> Array:
        mask = np.isin(e.gidx, common)
        return np.asarray(getattr(e, field), dtype=np.float64)[mask]

    n_obs = common.size
    log_path = _REPO_ROOT / "results" / "short-trial-log.jsonl"
    log_path.unlink(missing_ok=True)
    log = TrialLog(log_path)

    rows = []
    ls_cols = []
    excess_vs_lf = []
    ls_by_name = {}
    for e in evals:
        ls_strat = aligned(e, "ls_strat")
        ls_pos = aligned(e, "ls_pos")
        lf_strat = aligned(e, "lf_strat")
        log.append(TrialRecord(name=e.cfg.name, returns=[float(v) for v in ls_strat]))
        ls_cols.append(ls_strat)
        excess_vs_lf.append(ls_strat - lf_strat)
        ls_by_name[e.cfg.name] = ls_strat
        rows.append(
            (
                e.cfg.name,
                sharpe_ratio(ls_strat, _TRADING_DAYS),
                sharpe_ratio(lf_strat, _TRADING_DAYS),
                float(np.mean(ls_pos < 0)),  # fraction of bars short
                max_drawdown(ls_strat),
            )
        )

    rows.sort(key=lambda r: r[1], reverse=True)
    print(
        f"long/short search: {len(configs)} configs, OOS-within-research "
        f"({n_obs} aligned days, net funding)\n"
    )
    print(
        f"  {'config':16}{'L/S Sharpe':>12}{'L/F Sharpe':>12}"
        f"{'%short':>9}{'maxDD':>8}{'short helps':>12}"
    )
    for name, ls_sh, lf_sh, pshort, dd in rows:
        helps = "yes" if ls_sh > lf_sh else "no"
        print(
            f"  {name:16}{ls_sh:>+12.2f}{lf_sh:>+12.2f}"
            f"{pshort * 100:>8.0f}%{dd * 100:>7.1f}%{helps:>12}"
        )

    # --- multiple-testing correction over the long/short search ---
    best_name, best_ls_sharpe = rows[0][0], rows[0][1]
    best_strat = ls_by_name[best_name]
    trial_sharpes = log.sharpes()
    psr = probabilistic_sharpe_ratio(best_strat, 0.0, 1.0)
    dsr = deflated_sharpe_ratio(best_strat, trial_sharpes, 1.0)
    spa_cash = spa_test(np.column_stack(ls_cols), seed=42)
    spa_lf = spa_test(np.column_stack(excess_vs_lf), seed=42)  # does the short leg add value?
    pbo = cscv_pbo(np.column_stack(ls_cols)).pbo

    n_helps = sum(1 for _, ls_sh, lf_sh, _, _ in rows if ls_sh > lf_sh)
    print("\n=== MULTIPLE-TESTING VERDICT (net of funding, OOS-within-research) ===")
    print(f"  configs where shorting helps Sharpe (raw): {n_helps}/{len(configs)}")
    print(f"  best long/short: {best_name}  (Sharpe ann {best_ls_sharpe:+.2f})")
    print("  -- absolute edge (is the best long/short Sharpe real after the search?) --")
    print(f"  PSR (vs 0, uncorrected):          {psr:.3f}")
    print(f"  Deflated Sharpe ({len(configs)} configs):  {dsr:.3f}")
    print(f"  SPA p-value (best beats cash):    {spa_cash.spa_pvalue:.3f}")
    print("  -- relative (does the short leg beat the long/flat book?) --")
    print(f"  SPA p-value (best beats long/flat):{spa_lf.spa_pvalue:>6.3f}")
    print(f"  Reality Check p-value (conserv.): {spa_lf.reality_check_pvalue:.3f}")
    print(f"  PBO (CSCV, prob. backtest overfit):{pbo:>6.3f}")
    survives = dsr > 0.95 and spa_lf.spa_pvalue < 0.05
    verdict = (
        "short leg adds an edge that survives" if survives else "no edge survives the correction"
    )
    print(f"  --> {verdict}")

    # --- walk-forward: long/flat vs long/short for the default config ---
    research = close[:boundary]
    research_funding = funding[:boundary]
    ls_fn = functools.partial(causal_long_short_returns, short_bear=True)

    def wf(returns_fn) -> WalkForwardResult:  # type: ignore[no-untyped-def]
        return walk_forward_evaluate(
            research, **_WF, seed=42, funding_daily=research_funding, returns_fn=returns_fn
        )

    wf_lf = wf(causal_regime_returns)
    wf_ls = wf(ls_fn)
    print(
        f"\n=== WALK-FORWARD DISTRIBUTION "
        f"(research only, {wf_lf.n_windows} windows, net funding) ==="
    )
    print(f"  {'metric':24}{'long/flat':>12}{'long/short':>14}")
    for label, a, b in (
        ("pooled Sharpe", wf_lf.pooled_strat_sharpe, wf_ls.pooled_strat_sharpe),
        ("median window Sharpe", wf_lf.median_strat_sharpe, wf_ls.median_strat_sharpe),
        ("frac windows positive", wf_lf.frac_positive_return, wf_ls.frac_positive_return),
    ):
        print(f"  {label:24}{a:>+12.2f}{b:>+14.2f}")

    artifact = {
        "n_configs": len(configs),
        "n_obs": n_obs,
        "eval": "OOS-within-research (fit first half, causal second half, net of funding)",
        "best_long_short": best_name,
        "best_long_short_sharpe_ann": best_ls_sharpe,
        "configs_short_helps": n_helps,
        "psr_uncorrected": psr,
        "deflated_sharpe": dsr,
        "spa_pvalue_vs_cash": spa_cash.spa_pvalue,
        "spa_pvalue_vs_long_flat": spa_lf.spa_pvalue,
        "reality_check_pvalue_vs_long_flat": spa_lf.reality_check_pvalue,
        "pbo": pbo,
        "edge_survives_correction": survives,
        "walk_forward": {
            "n_windows": wf_lf.n_windows,
            "pooled_sharpe_long_flat": wf_lf.pooled_strat_sharpe,
            "pooled_sharpe_long_short": wf_ls.pooled_strat_sharpe,
            "median_window_sharpe_long_flat": wf_lf.median_strat_sharpe,
            "median_window_sharpe_long_short": wf_ls.median_strat_sharpe,
        },
    }
    out = _REPO_ROOT / "results" / "short-eval.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\n  artifacts: {out}  and  {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
