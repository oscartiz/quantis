"""Re-run the holdout and walk-forward evaluations net of *real* HL funding.

The bundled regime strategy's returns are close-to-close only; a long-only perp
also pays funding for every day it holds. This script quantifies that drag using
the hash-pinned ``data/sample/btc-funding.csv`` (real Hyperliquid BTC funding,
summed per UTC day, cadence-agnostic), reporting every headline number both
gross and net of funding so the cost is explicit, not hidden.

Run (from repo root):
    uv run --project python python python/scripts/evaluate_funding_impact.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from quantis.data.candles import load_candles
from quantis.data.funding import load_daily_funding
from quantis.data.holdout import ACKNOWLEDGEMENT, HoldoutManifest, load_research, reveal_holdout
from quantis.evaluation.metrics import max_drawdown, sharpe_ratio
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm
from quantis.evaluation.walk_forward import walk_forward_evaluate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 365


def _ret(strat: np.ndarray) -> float:
    return float(np.exp(np.sum(strat)) - 1.0)


def main() -> int:
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    manifest = HoldoutManifest.from_json(_REPO_ROOT / "data" / "sample" / "holdout-manifest.json")
    funding = load_daily_funding(
        _REPO_ROOT / "data" / "sample" / "btc-funding.csv", candles.dates_iso()
    )
    covered = int(np.sum(funding != 0.0))
    print(
        f"funding: {covered}/{len(funding)} days covered, "
        f"mean {funding[funding != 0].mean() * 100:+.4f}%/day "
        f"({funding[funding != 0].mean() * 365 * 100:+.1f}%/yr); "
        "longs pay on positive days\n"
    )

    # ---------- one-shot holdout: gross vs net ----------
    research_close = load_research(candles.close, manifest)
    model = fit_regime_hmm(research_close, seed=42)
    holdout_close = reveal_holdout(candles.close, manifest, ACKNOWLEDGEMENT)
    f_holdout = funding[manifest.boundary_index :]

    g = causal_regime_returns(model, holdout_close)
    n = causal_regime_returns(model, holdout_close, funding_daily=f_holdout)
    long_days = int(np.sum(g.position))
    in_mkt = float(np.mean(g.position))
    fund_drag = _ret(g.strat) - _ret(n.strat)
    print("=== ONE-SHOT HOLDOUT (gross -> net of funding) ===")
    print(f"  long-days: {long_days} of {len(g.position)}  ({in_mkt * 100:.0f}% in mkt)")
    print(
        f"  total return:  {_ret(g.strat) * 100:+.1f}%  ->  {_ret(n.strat) * 100:+.1f}%  "
        f"(funding drag {fund_drag * 100:.1f} pts)"
    )
    print(
        f"  Sharpe (ann):  {sharpe_ratio(g.strat, _TRADING_DAYS):+.2f}  ->  "
        f"{sharpe_ratio(n.strat, _TRADING_DAYS):+.2f}      "
        f"(buy & hold {sharpe_ratio(g.hold, _TRADING_DAYS):+.2f}, {_ret(g.hold) * 100:+.1f}%)"
    )
    print(
        f"  max drawdown:  {max_drawdown(g.strat) * 100:.1f}%  ->  "
        f"{max_drawdown(n.strat) * 100:.1f}%\n"
    )

    # ---------- walk-forward distribution: gross vs net ----------
    wg = walk_forward_evaluate(candles.close, train_min=365, test_window=90, step=45, seed=42)
    wn = walk_forward_evaluate(
        candles.close, train_min=365, test_window=90, step=45, seed=42, funding_daily=funding
    )
    print("=== WALK-FORWARD (20 quarterly OOS windows; gross -> net of funding) ===")
    print(f"  mean time in market: {wg.mean_time_in_market * 100:.0f}%")
    print(
        f"  pooled OOS Sharpe:   {wg.pooled_strat_sharpe:+.2f}  ->  "
        f"{wn.pooled_strat_sharpe:+.2f}      (buy & hold {wg.pooled_hold_sharpe:+.2f})"
    )
    print(
        f"  pooled OOS return:   {wg.pooled_strat_total_return * 100:+.1f}%  ->  "
        f"{wn.pooled_strat_total_return * 100:+.1f}%   "
        f"(buy & hold {wg.pooled_hold_total_return * 100:+.1f}%)"
    )
    print(
        f"  median window Sharpe:{wg.median_strat_sharpe:+.2f}  ->  "
        f"{wn.median_strat_sharpe:+.2f}   "
        f"| windows positive: {wg.frac_positive_return * 100:.0f}%  ->  "
        f"{wn.frac_positive_return * 100:.0f}%"
    )
    print(
        f"  windows beating buy&hold Sharpe: {wg.frac_beat_hold_sharpe * 100:.0f}%  ->  "
        f"{wn.frac_beat_hold_sharpe * 100:.0f}%"
    )

    artifact = {
        "funding_days_covered": covered,
        "holdout": {
            "long_days": long_days,
            "total_return_gross": _ret(g.strat),
            "total_return_net": _ret(n.strat),
            "sharpe_gross": sharpe_ratio(g.strat, _TRADING_DAYS),
            "sharpe_net": sharpe_ratio(n.strat, _TRADING_DAYS),
            "max_drawdown_net": max_drawdown(n.strat),
            "buy_hold_return": _ret(g.hold),
            "buy_hold_sharpe": sharpe_ratio(g.hold, _TRADING_DAYS),
        },
        "walk_forward": {
            "pooled_sharpe_gross": wg.pooled_strat_sharpe,
            "pooled_sharpe_net": wn.pooled_strat_sharpe,
            "pooled_return_gross": wg.pooled_strat_total_return,
            "pooled_return_net": wn.pooled_strat_total_return,
            "pooled_hold_sharpe": wg.pooled_hold_sharpe,
            "pooled_hold_return": wg.pooled_hold_total_return,
            "frac_positive_gross": wg.frac_positive_return,
            "frac_positive_net": wn.frac_positive_return,
        },
    }
    out = _REPO_ROOT / "results" / "funding-impact.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\n  artifact: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
