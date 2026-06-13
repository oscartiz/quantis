"""Feature pipeline: causality (the leakage canary), correctness, and config
wiring. The canary is the load-bearing test — it proves the leakage check
catches a feature that peeks forward, which is how a real leak would be caught.
"""

import numpy as np
import pytest
from numpy.typing import NDArray

from quantis.config import FeatureSpec
from quantis.features import (
    LeakageError,
    assert_causal,
    build_features,
    is_causal,
)
from quantis.features.pipeline import (
    Array,
    log_return,
    momentum,
    realized_vol,
    sma,
    zscore,
)


@pytest.fixture
def price_series() -> Array:
    rng = np.random.default_rng(42)
    steps = rng.normal(0.0, 0.001, size=500)
    return np.asarray(100_000.0 * np.exp(np.cumsum(steps)), dtype=np.float64)


# --- the leakage canary -------------------------------------------------------


def future_return(series: Array, horizon: int = 1) -> Array:
    """DELIBERATELY LEAKY: uses data from `horizon` steps in the FUTURE. Exists
    only to prove the leakage check catches look-ahead. Never registered."""
    series = np.asarray(series, dtype=np.float64)
    out = np.full_like(series, np.nan)
    out[:-horizon] = series[horizon:] / series[:-horizon] - 1.0
    return out


def centered_mean(series: Array, window: int = 5) -> Array:
    """DELIBERATELY LEAKY: a centered window straddles the present, using
    future points. Classic accidental look-ahead."""
    series = np.asarray(series, dtype=np.float64)
    out = np.full_like(series, np.nan)
    half = window // 2
    for t in range(half, len(series) - half):
        out[t] = np.mean(series[t - half : t + half + 1])
    return out


def test_canary_flags_forward_peeking_feature(price_series: Array) -> None:
    assert not is_causal(future_return, price_series, {"horizon": 1})
    assert not is_causal(centered_mean, price_series, {"window": 5})
    with pytest.raises(LeakageError):
        assert_causal(future_return, price_series, {"horizon": 3})


def test_real_features_pass_the_causality_check(price_series: Array) -> None:
    assert is_causal(log_return, price_series, {"lag": 1})
    assert is_causal(realized_vol, price_series, {"window": 30})
    assert is_causal(sma, price_series, {"window": 20})
    assert is_causal(momentum, price_series, {"window": 15})
    assert is_causal(zscore, price_series, {"window": 30})


# --- correctness --------------------------------------------------------------


def test_log_return_values_and_warmup() -> None:
    series: Array = np.array([1.0, np.e, np.e**2], dtype=np.float64)
    out = log_return(series, lag=1)
    assert np.isnan(out[0])
    assert np.allclose(out[1:], [1.0, 1.0])


def test_warmup_positions_are_nan_not_zero(price_series: Array) -> None:
    rv = realized_vol(price_series, window=30)
    # need 1 (for return) + 30 (for window) warmup positions of NaN
    assert np.all(np.isnan(rv[:30]))
    assert np.all(np.isfinite(rv[31:]))


def test_realized_vol_matches_manual_std(price_series: Array) -> None:
    window = 30
    rv = realized_vol(price_series, window=window)
    rets = np.diff(np.log(price_series))
    t = 200
    # rv[t] uses returns ending at t, i.e. rets index (t-1) back `window`
    manual = np.std(rets[t - window : t])
    assert np.isclose(rv[t], manual)


# --- config wiring ------------------------------------------------------------


def test_build_features_from_specs(price_series: Array) -> None:
    specs = [
        FeatureSpec(name="log_return", params={"lag": 1}),
        FeatureSpec(name="realized_vol", params={"window": 30}),
        FeatureSpec(name="zscore", params={"window": 30}),
    ]
    fm = build_features(price_series, specs)
    assert fm.names == ["log_return_lag1", "realized_vol_window30", "zscore_window30"]
    assert fm.values.shape == (len(price_series), 3)
    # warmed rows have all-finite features, and there are some
    warmed: NDArray[np.float64] = fm.warmed()
    assert warmed.shape[0] > 0
    assert np.all(np.isfinite(warmed))


def test_unknown_feature_is_rejected(price_series: Array) -> None:
    with pytest.raises(KeyError, match="secret_signal"):
        build_features(price_series, [FeatureSpec(name="secret_signal")])
