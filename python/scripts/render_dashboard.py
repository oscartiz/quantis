"""Render the research dashboard from the bundled candle data.

Usage (from the repo root):
    uv run --project python python python/scripts/render_dashboard.py

Writes results/dashboard.html — open it in any browser, fully offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

from quantis.dashboard import generate_report

_REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    candles = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"
    out = _REPO_ROOT / "results" / "dashboard.html"
    path = generate_report(candles, out, seed=42)
    print(f"dashboard written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
