"""Candle loader: parses the bundled CSV, stays time-ordered, and yields a
sensible log-return series."""

from pathlib import Path

import numpy as np
import pytest

from quantis.data.candles import load_candles

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDLES = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"


def test_loads_bundled_candles() -> None:
    c = load_candles(_CANDLES)
    assert len(c) > 1000
    # time-ordered ascending
    assert np.all(np.diff(c.open_ms) > 0)
    # OHLC sanity: high >= low, close within [low, high]
    assert np.all(c.high >= c.low)
    assert np.all((c.close <= c.high + 1e-6) & (c.close >= c.low - 1e-6))
    # BTC over this span stays in a plausible range
    assert c.close.min() > 5_000
    assert c.close.max() < 500_000


def test_log_returns_shape_and_finiteness() -> None:
    c = load_candles(_CANDLES)
    r = c.log_returns()
    assert r.shape[0] == len(c) - 1
    assert np.all(np.isfinite(r))


def test_dates_align_with_candles() -> None:
    c = load_candles(_CANDLES)
    dates = c.dates_iso()
    assert len(dates) == len(c)
    assert dates[0].startswith("2023-")


def test_rejects_empty_file(tmp_path: object) -> None:
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    p = tmp_path / "empty.csv"
    p.write_text("open_ms,open,high,low,close,volume\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no candles"):
        load_candles(p)
