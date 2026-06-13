"""The one-shot holdout evaluation.

Discipline: the holdout manifest was committed before this ran. This script
fits the regime model on the RESEARCH partition only, then evaluates the causal
regime strategy on the sealed HOLDOUT exactly once, and writes the result
artifact. Whatever number it produces is reported as-is — a mediocre honest
out-of-sample result is the point, not a failure.

Run (from repo root):
    uv run --project python python python/scripts/evaluate_holdout.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from quantis.data.candles import load_candles
from quantis.data.holdout import ACKNOWLEDGEMENT, HoldoutManifest, load_research, reveal_holdout
from quantis.evaluation.metrics import max_drawdown, sharpe_ratio, sortino_ratio
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 365


@dataclass(frozen=True)
class HoldoutResult:
    """The single out-of-sample evaluation outcome."""

    research_days: int
    holdout_days: int
    holdout_sharpe: float
    holdout_sortino: float
    holdout_max_drawdown: float
    buy_hold_sharpe: float
    buy_hold_max_drawdown: float
    strategy_total_return: float
    buy_hold_total_return: float
    time_in_market: float


def main() -> int:
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    manifest = HoldoutManifest.from_json(_REPO_ROOT / "data" / "sample" / "holdout-manifest.json")

    # Research partition only — load_research refuses anything past the boundary.
    research_close = load_research(candles.close, manifest)

    # Fit the regime model on research data ONLY.
    model = fit_regime_hmm(research_close, seed=42)

    # Reveal the holdout exactly once (verifies the committed hash), then
    # evaluate the causal strategy under the research-fit model.
    holdout_close = reveal_holdout(candles.close, manifest, ACKNOWLEDGEMENT)
    r = causal_regime_returns(model, holdout_close)
    strat, hold, position = r.strat, r.hold, r.position

    result = HoldoutResult(
        research_days=manifest.n_research,
        holdout_days=int(strat.shape[0]),
        holdout_sharpe=sharpe_ratio(strat, _TRADING_DAYS),
        holdout_sortino=sortino_ratio(strat, _TRADING_DAYS),
        holdout_max_drawdown=max_drawdown(strat),
        buy_hold_sharpe=sharpe_ratio(hold, _TRADING_DAYS),
        buy_hold_max_drawdown=max_drawdown(hold),
        strategy_total_return=float(np.exp(np.sum(strat)) - 1.0),
        buy_hold_total_return=float(np.exp(np.sum(hold)) - 1.0),
        time_in_market=float(np.mean(position)),
    )

    out = _REPO_ROOT / "results" / "holdout-evaluation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")

    print("=== ONE-SHOT HOLDOUT EVALUATION (reported as-is) ===")
    print(f"  research: {result.research_days} days   holdout: {result.holdout_days} days")
    print(
        f"  holdout Sharpe (ann.):   {result.holdout_sharpe:+.2f}   "
        f"(buy & hold: {result.buy_hold_sharpe:+.2f})"
    )
    print(f"  holdout Sortino (ann.):  {result.holdout_sortino:+.2f}")
    print(
        f"  holdout max drawdown:    {result.holdout_max_drawdown * 100:.1f}%   "
        f"(buy & hold: {result.buy_hold_max_drawdown * 100:.1f}%)"
    )
    print(
        f"  holdout total return:    {result.strategy_total_return * 100:+.1f}%   "
        f"(buy & hold: {result.buy_hold_total_return * 100:+.1f}%)"
    )
    print(f"  time in market:          {result.time_in_market * 100:.0f}%")
    print(f"  artifact: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
