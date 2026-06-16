"""Loader for bundled Hyperliquid perpetual funding history.

A perpetual position pays (or receives) funding at each funding event; a *long*
pays when the rate is positive. The bundled regime strategy is long-only, so
funding is a one-sided drag whenever it holds — material over multi-week holds
and entirely absent from the close-to-close return series. This module turns the
raw, hash-pinned funding events (``data/sample/btc-funding.csv``) into a per-day
cost aligned to a candle date series.

The funding cadence is **not** assumed: HL used 8-hour intervals early and
switched to hourly later, so the daily cost is the *sum of every event's rate
within that UTC day*, robust to the cadence change. Days with no funding data
(the pre-2023-05-12 backfilled candles) are 0 — we never charge funding we do
not have.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


def _utc_day(time_ms: int) -> str:
    return dt.datetime.fromtimestamp(time_ms / 1000, tz=dt.UTC).strftime("%Y-%m-%d")


def load_daily_funding(path: str | Path, dates: list[str]) -> Array:
    """Per-UTC-day funding cost (fraction; positive = a long pays), aligned to
    ``dates``. Uncovered days (no events) are 0.0.

    ``dates`` are ``YYYY-MM-DD`` strings, e.g. ``Candles.dates_iso()``.
    """
    daily: dict[str, float] = defaultdict(float)
    with Path(path).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            daily[_utc_day(int(row["time_ms"]))] += float(row["funding_rate"])
    return np.array([daily.get(d, 0.0) for d in dates], dtype=np.float64)
