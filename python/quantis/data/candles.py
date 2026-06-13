"""Loader for bundled OHLCV candle history (the regime-research dataset).

The 15-minute L2 sample exists for backtest fill fidelity; regime models want
*bar* history over a long enough span to contain real bull/bear/chop. This
loads the committed daily-candle CSV into aligned arrays and the log-return
series the models consume.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


@dataclass(frozen=True)
class Candles:
    """Aligned OHLCV candle series, time-ordered ascending."""

    open_ms: NDArray[np.int64]
    open: Array
    high: Array
    low: Array
    close: Array
    volume: Array

    def __len__(self) -> int:
        return int(self.close.shape[0])

    def log_returns(self) -> Array:
        """Close-to-close log returns; length ``len(self) - 1``."""
        return np.diff(np.log(self.close))

    def dates_iso(self) -> list[str]:
        """Open timestamps as ``YYYY-MM-DD`` strings (UTC)."""
        import datetime as dt

        return [
            dt.datetime.fromtimestamp(t / 1000, tz=dt.UTC).strftime("%Y-%m-%d")
            for t in self.open_ms
        ]


def load_candles(path: str | Path) -> Candles:
    """Load a candle CSV with header ``open_ms,open,high,low,close,volume``."""
    rows: list[tuple[int, float, float, float, float, float]] = []
    with Path(path).open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            rows.append(
                (
                    int(r["open_ms"]),
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                    float(r["volume"]),
                )
            )
    if not rows:
        raise ValueError(f"no candles in {path}")
    rows.sort(key=lambda x: x[0])
    arr = np.array(rows, dtype=np.float64)
    if np.any(arr[:, 4] <= 0):
        raise ValueError("non-positive close prices present")
    return Candles(
        open_ms=arr[:, 0].astype(np.int64),
        open=arr[:, 1],
        high=arr[:, 2],
        low=arr[:, 3],
        close=arr[:, 4],
        volume=arr[:, 5],
    )
