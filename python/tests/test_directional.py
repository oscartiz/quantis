"""Long/short regime strategy: a strict generalization of long/flat that shorts
the bear regime, prices a short's funding with the right sign, and stays causal."""

from pathlib import Path

import numpy as np
import pytest

from quantis.data.candles import load_candles
from quantis.evaluation.directional_strategy import causal_long_short_returns
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDLES = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"
_VOL_WINDOW = 20


@pytest.fixture(scope="module")
def close() -> np.ndarray:
    return load_candles(_CANDLES).close


@pytest.fixture(scope="module")
def fitted(close: np.ndarray):  # type: ignore[no-untyped-def]
    return fit_regime_hmm(close[:600], seed=42, vol_window=_VOL_WINDOW)


def test_no_short_recovers_long_flat(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    base = causal_regime_returns(fitted, close, vol_window=_VOL_WINDOW)
    ls = causal_long_short_returns(fitted, close, vol_window=_VOL_WINDOW, short_bear=False)
    assert np.array_equal(base.candle_index, ls.candle_index)
    assert np.array_equal(base.position, ls.position)
    assert np.allclose(base.strat, ls.strat)


def test_shorting_adds_short_positions_only(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    """Enabling shorts introduces -1 positions and never changes the +1 (bull)
    rows — it only fills some previously-flat bear bars with shorts."""
    lf = causal_long_short_returns(fitted, close, vol_window=_VOL_WINDOW, short_bear=False)
    ls = causal_long_short_returns(fitted, close, vol_window=_VOL_WINDOW, short_bear=True)
    assert np.any(ls.position < 0)
    assert set(np.unique(ls.position)).issubset({-1.0, 0.0, 1.0})
    longs = lf.position > 0
    assert np.array_equal(ls.position[longs], lf.position[longs])  # bull rows untouched
    assert np.all(ls.position[~longs] <= 0)  # the rest is flat or short


def test_funding_helps_shorts_and_hurts_longs(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    """Adding +0.1%/day funding must *lower* the return of long bars (a long pays)
    and *raise* the return of short bars (a short receives) by exactly the rate —
    the sign convention that makes shorting positive-funding regimes legitimate."""
    funding = np.full(close.shape[0], 0.001)
    gross = causal_long_short_returns(fitted, close, vol_window=_VOL_WINDOW, short_bear=True)
    net = causal_long_short_returns(
        fitted, close, vol_window=_VOL_WINDOW, funding_daily=funding, short_bear=True
    )
    diff = net.strat - gross.strat  # = -position · 0.001
    pos = gross.position
    assert np.all(diff[pos > 0] < 0.0)  # longs pay
    assert np.all(diff[pos < 0] > 0.0)  # shorts receive
    assert np.allclose(diff[pos == 0], 0.0)  # flat unaffected
    assert np.allclose(diff, -pos * 0.001)


def test_prefix_causal(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    full = causal_long_short_returns(fitted, close, vol_window=_VOL_WINDOW, short_bear=True)
    for k in (400, 800):
        prefix = causal_long_short_returns(
            fitted, close[:k], vol_window=_VOL_WINDOW, short_bear=True
        )
        m = prefix.candle_index.size - 1
        assert np.array_equal(prefix.candle_index[:m], full.candle_index[:m])
        assert np.array_equal(prefix.position[:m], full.position[:m])


def test_deterministic(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    a = causal_long_short_returns(fitted, close, vol_window=_VOL_WINDOW)
    b = causal_long_short_returns(fitted, close, vol_window=_VOL_WINDOW)
    assert np.array_equal(a.position, b.position)
    assert np.array_equal(a.strat, b.strat)
