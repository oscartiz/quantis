"""HMM + BOCPD ensemble overlay: it only ever de-risks, it is causal end to
end, and its changepoint gate resets on a real volatility break.

The overlay's whole claim is that it can subtract exposure faster than the
smooth HMM filter without ever peeking forward. These tests pin both halves:
the de-risk-only invariant and prefix-causality (the same property
``tests/test_bocpd.py`` asserts for BOCPD itself, carried through the
vol-standardization and the gate)."""

from pathlib import Path

import numpy as np
import pytest

from quantis.data.candles import load_candles
from quantis.evaluation.ensemble_strategy import (
    causal_ensemble_returns,
    causal_run_length,
)
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDLES = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"
_VOL_WINDOW = 20


@pytest.fixture(scope="module")
def close() -> np.ndarray:
    return load_candles(_CANDLES).close


@pytest.fixture(scope="module")
def fitted(close: np.ndarray):  # type: ignore[no-untyped-def]
    # Fit once on an early prefix; every test evaluates this fixed model causally.
    return fit_regime_hmm(close[:600], seed=42, vol_window=_VOL_WINDOW)


def test_overlay_only_de_risks(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    """The BOCPD gate can turn long -> flat but never flat -> long, so the
    ensemble position is dominated by the HMM position bar for bar."""
    hmm = causal_regime_returns(fitted, close, vol_window=_VOL_WINDOW)
    ens = causal_ensemble_returns(fitted, close, vol_window=_VOL_WINDOW, min_run_length=5)
    assert np.array_equal(hmm.candle_index, ens.candle_index)
    assert np.all(ens.position <= hmm.position + 1e-12)
    assert ens.position.mean() <= hmm.position.mean()
    # The gate is not a no-op on this history: it removes at least one long bar.
    assert ens.position.sum() < hmm.position.sum()


def test_min_run_length_zero_recovers_the_hmm(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    """With ``min_run_length=0`` every segment is 'confirmed' immediately, so the
    overlay collapses exactly to the HMM filter — proving it is a strict
    generalization whose only effect is the changepoint gate."""
    hmm = causal_regime_returns(fitted, close, vol_window=_VOL_WINDOW)
    ens = causal_ensemble_returns(fitted, close, vol_window=_VOL_WINDOW, min_run_length=0)
    assert np.array_equal(hmm.position, ens.position)
    assert np.allclose(hmm.strat, ens.strat)
    assert np.allclose(hmm.hold, ens.hold)


def test_run_length_is_prefix_causal(close: np.ndarray) -> None:
    """BOCPD's MAP run length at index t depends only on close[:t+1]; carried
    through the vol-standardization it must match whether computed on a prefix
    or the full series. A leak would change past values when the future arrives."""
    full = causal_run_length(close, vol_window=_VOL_WINDOW)
    for k in (200, 400, 800):
        prefix = causal_run_length(close[:k], vol_window=_VOL_WINDOW)
        assert np.array_equal(prefix, full[:k]), f"run length not prefix-causal at k={k}"


def test_ensemble_position_is_prefix_causal(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: under a fixed model, the position decided for a given candle
    is identical whether the strategy sees the full series or only up to a later
    cut. (The final row is dropped for lack of a next candle, so compare the
    overlap excluding the prefix's last decision.)"""
    full = causal_ensemble_returns(fitted, close, vol_window=_VOL_WINDOW)
    for k in (400, 800):
        prefix = causal_ensemble_returns(fitted, close[:k], vol_window=_VOL_WINDOW)
        m = prefix.candle_index.size - 1  # drop the prefix's last (boundary) row
        assert np.array_equal(prefix.candle_index[:m], full.candle_index[:m])
        assert np.array_equal(prefix.position[:m], full.position[:m])


def test_run_length_resets_on_a_volatility_break() -> None:
    """A calm uptrend followed by a sharp crash: the run length should grow long
    during the calm segment and collapse right after the break — the signal the
    overlay turns into a fast exit."""
    rng = np.random.default_rng(0)
    calm = 0.0008 + 0.004 * rng.standard_normal(220)  # gentle drift, low vol
    crash = np.array([-0.09, -0.07, -0.11, -0.06, -0.08])  # an abrupt regime break
    after = -0.001 + 0.03 * rng.standard_normal(60)  # noisy, elevated vol
    rets = np.concatenate([calm, crash, after])
    close = 30_000.0 * np.exp(np.cumsum(rets))

    rl = causal_run_length(close, vol_window=_VOL_WINDOW)
    break_idx = len(calm)  # first crash bar (index into close)
    # Long, stable run length built up over the calm segment...
    assert rl[break_idx - 1] >= 20
    # ...and a confident reset within a few bars of the break.
    assert rl[break_idx : break_idx + 6].min() <= 3


def test_deterministic(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    a = causal_ensemble_returns(fitted, close, vol_window=_VOL_WINDOW)
    b = causal_ensemble_returns(fitted, close, vol_window=_VOL_WINDOW)
    assert np.array_equal(a.position, b.position)
    assert np.array_equal(a.strat, b.strat)
