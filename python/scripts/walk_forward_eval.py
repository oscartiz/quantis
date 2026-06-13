"""Walk-forward out-of-sample evaluation of the regime strategy.

Turns the single sealed holdout into a distribution: refit-and-evaluate over
many rolling out-of-sample windows and report the spread, not a lucky draw.

Run (from repo root):
    uv run --project python python python/scripts/walk_forward_eval.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from quantis.data.candles import load_candles
from quantis.evaluation.walk_forward import walk_forward_evaluate

_REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    close = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv").close
    # ~quarterly OOS windows, refit every ~6 weeks, after 1 year of training.
    res = walk_forward_evaluate(close, train_min=365, test_window=90, step=45, seed=42)

    print("=== WALK-FORWARD OUT-OF-SAMPLE (the distribution behind the holdout) ===")
    print(f"  windows: {res.n_windows}  (quarterly OOS, refit every ~45 days)")
    print(
        f"  per-window strat Sharpe:  mean {res.mean_strat_sharpe:+.2f}  "
        f"median {res.median_strat_sharpe:+.2f}  std {res.std_strat_sharpe:.2f}"
    )
    print(f"  windows with positive return:  {res.frac_positive_return * 100:.0f}%")
    print(f"  windows beating buy&hold Sharpe: {res.frac_beat_hold_sharpe * 100:.0f}%")
    print(f"  mean time in market:           {res.mean_time_in_market * 100:.0f}%")
    print(
        f"  POOLED OOS:  strat Sharpe {res.pooled_strat_sharpe:+.2f} "
        f"(hold {res.pooled_hold_sharpe:+.2f}),  "
        f"strat return {res.pooled_strat_total_return * 100:+.1f}% "
        f"(hold {res.pooled_hold_total_return * 100:+.1f}%)"
    )

    out = _REPO_ROOT / "results" / "walk-forward-evaluation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(res), indent=2) + "\n", encoding="utf-8")
    print(f"  artifact: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
