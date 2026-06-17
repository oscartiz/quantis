"""Continuous vol-targeted / conviction sizing: a strict generalization of the
binary regime strategy that stays causal and bounded.

The load-bearing claims: with sizing off it *is* the binary strategy; with
sizing on the weight is exactly the risk crate's vol-target clamp (re-derived
here to pin the formula); conviction weighting uses the filtered posterior; and
the whole thing is prefix-causal under a fixed model."""

from pathlib import Path

import numpy as np
import pytest

from quantis.data.candles import load_candles
from quantis.evaluation.regime_strategy import (
    causal_regime_returns,
    fit_regime_hmm,
    ordered_regimes,
    regime_features,
)
from quantis.evaluation.sizing_strategy import (
    DEFAULT_MAX_LEVERAGE,
    causal_position_weight,
    causal_sized_returns,
)
from quantis.features.pipeline import realized_vol

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDLES = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"
_VOL_WINDOW = 20
_TARGET_VOL = 0.02


@pytest.fixture(scope="module")
def close() -> np.ndarray:
    return load_candles(_CANDLES).close


@pytest.fixture(scope="module")
def fitted(close: np.ndarray):  # type: ignore[no-untyped-def]
    return fit_regime_hmm(close[:600], seed=42, vol_window=_VOL_WINDOW)


def test_baseline_equivalence_to_binary_strategy(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    """target_vol=None + no conviction is the binary long/flat strategy exactly,
    so sizing is a strict generalization that adds nothing until asked to."""
    base = causal_regime_returns(fitted, close, vol_window=_VOL_WINDOW)
    sized = causal_sized_returns(fitted, close, vol_window=_VOL_WINDOW, target_vol=None)
    assert np.array_equal(base.candle_index, sized.candle_index)
    assert np.array_equal(base.position, sized.position)
    assert np.allclose(base.strat, sized.strat)
    assert np.allclose(base.hold, sized.hold)


def test_weights_are_bounded_long_only(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    for conv in (False, True):
        w, _ = causal_position_weight(
            fitted, close, target_vol=_TARGET_VOL, vol_window=_VOL_WINDOW, conviction_weighted=conv
        )
        assert np.all(w >= 0.0)
        assert np.all(w <= DEFAULT_MAX_LEVERAGE + 1e-12)


def test_vol_target_weight_matches_the_risk_crate_formula(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    """Binary signal x vol-target clamp == clamp(target_vol/realized_vol) on bull
    bars, 0 otherwise — the equity-weight form of crate::risk::vol_target_qty."""
    w, valid = causal_position_weight(
        fitted,
        close,
        target_vol=_TARGET_VOL,
        vol_window=_VOL_WINDOW,
        max_leverage=DEFAULT_MAX_LEVERAGE,
    )
    fm = regime_features(close, _VOL_WINDOW)
    bull = (ordered_regimes(fitted, fitted.filter_proba(fm.values[valid])) == 2).astype(float)
    rv = realized_vol(close, window=_VOL_WINDOW)[valid]
    expected = bull * np.clip(_TARGET_VOL / rv, 0.0, DEFAULT_MAX_LEVERAGE)
    assert np.allclose(w, expected)
    # Where held, the weight moves inversely with realized vol (calmer -> bigger).
    held = w > 0
    assert np.corrcoef(rv[held], w[held])[0, 1] < 0.0


def test_leverage_cap_binds_when_target_is_large(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    w, _ = causal_position_weight(
        fitted, close, target_vol=10.0, vol_window=_VOL_WINDOW, max_leverage=DEFAULT_MAX_LEVERAGE
    )
    held = w > 0
    assert held.any()
    assert np.allclose(w[held], DEFAULT_MAX_LEVERAGE)  # absurd target -> cap everywhere held


def test_conviction_weighting_uses_the_posterior(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    w, valid = causal_position_weight(
        fitted, close, target_vol=_TARGET_VOL, vol_window=_VOL_WINDOW, conviction_weighted=True
    )
    fm = regime_features(close, _VOL_WINDOW)
    proba = fitted.filter_proba(fm.values[valid])
    p_bull = proba[:, int(fitted.regime_order()[2])]
    rv = realized_vol(close, window=_VOL_WINDOW)[valid]
    expected = p_bull * np.clip(_TARGET_VOL / rv, 0.0, DEFAULT_MAX_LEVERAGE)
    assert np.allclose(w, expected)
    # Conviction holds fractional positions the binary rule would not: it differs.
    binary, _ = causal_position_weight(
        fitted, close, target_vol=_TARGET_VOL, vol_window=_VOL_WINDOW, conviction_weighted=False
    )
    assert not np.allclose(w, binary)


def test_position_is_prefix_causal(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    full = causal_sized_returns(
        fitted, close, vol_window=_VOL_WINDOW, target_vol=_TARGET_VOL, conviction_weighted=True
    )
    for k in (400, 800):
        prefix = causal_sized_returns(
            fitted,
            close[:k],
            vol_window=_VOL_WINDOW,
            target_vol=_TARGET_VOL,
            conviction_weighted=True,
        )
        m = prefix.candle_index.size - 1  # drop the prefix's boundary row
        assert np.array_equal(prefix.candle_index[:m], full.candle_index[:m])
        assert np.allclose(prefix.position[:m], full.position[:m])


def test_deterministic(fitted, close: np.ndarray) -> None:  # type: ignore[no-untyped-def]
    a = causal_sized_returns(fitted, close, vol_window=_VOL_WINDOW, target_vol=_TARGET_VOL)
    b = causal_sized_returns(fitted, close, vol_window=_VOL_WINDOW, target_vol=_TARGET_VOL)
    assert np.array_equal(a.position, b.position)
    assert np.array_equal(a.strat, b.strat)
