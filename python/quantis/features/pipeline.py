"""Config-driven, causal-by-construction feature pipeline.

Every feature here is a pure function of a price series whose value at time
``t`` depends only on data up to and including ``t``; warmup positions are
``NaN`` and never forward-filled from the future. Feature names and parameters
come from YAML (``quantis.config.FeatureSpec``) — there are no magic numbers in
this module.

The honesty mechanism is :func:`is_causal` / :func:`assert_causal`: a causal
feature computed on a prefix of the data must equal the full-series feature at
the overlapping indices. Any feature that peeks forward fails this check. The
canary test (``tests/test_features.py``) registers a deliberately leaky feature
and proves the check catches it — so a real leak in a real feature would be
caught the same way.
"""

from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from quantis.config import FeatureSpec

Array = NDArray[np.float64]
FeatureFn = Callable[..., Array]

# --- causal feature primitives ------------------------------------------------


def _as_f64(series: Array) -> Array:
    arr = np.asarray(series, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError("feature input must be a 1-D series")
    return arr


def _rolling(series: Array, window: int) -> Array:
    """(n, window) view where row t holds series[t-window+1 .. t]; rows before
    the window is full are NaN-padded. Strictly backward-looking."""
    if window < 1:
        raise ValueError("window must be >= 1")
    n = series.shape[0]
    out = np.full((n, window), np.nan, dtype=np.float64)
    for offset in range(window):
        # column `offset` is the value `window-1-offset` steps in the past
        lag = window - 1 - offset
        if lag == 0:
            out[:, offset] = series
        else:
            out[lag:, offset] = series[:-lag]
    return out


def log_return(series: Array, lag: int = 1) -> Array:
    """log(p_t / p_{t-lag}); first `lag` positions NaN."""
    series = _as_f64(series)
    out = np.full_like(series, np.nan)
    if lag < 1:
        raise ValueError("lag must be >= 1")
    out[lag:] = np.log(series[lag:] / series[:-lag])
    return out


def realized_vol(series: Array, window: int = 60) -> Array:
    """Rolling standard deviation of 1-step log returns over `window`."""
    series = _as_f64(series)
    rets = log_return(series, lag=1)
    win = _rolling(rets, window)
    # population std over the window; NaN where the window is not full
    out: Array = np.std(win, axis=1)
    return out


def sma(series: Array, window: int = 20) -> Array:
    """Simple moving average over `window`."""
    series = _as_f64(series)
    out: Array = np.mean(_rolling(series, window), axis=1)
    return out


def momentum(series: Array, window: int = 60) -> Array:
    """log(p_t / p_{t-window}); momentum over `window`."""
    series = _as_f64(series)
    return log_return(series, lag=window)


def zscore(series: Array, window: int = 60) -> Array:
    """(p_t - rolling_mean) / rolling_std over `window`."""
    series = _as_f64(series)
    win = _rolling(series, window)
    mean = np.mean(win, axis=1)
    std = np.std(win, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out: Array = (series - mean) / std
    return out


_REGISTRY: dict[str, FeatureFn] = {
    "log_return": log_return,
    "realized_vol": realized_vol,
    "sma": sma,
    "momentum": momentum,
    "zscore": zscore,
}


def available_features() -> list[str]:
    """Names of registered causal features."""
    return sorted(_REGISTRY)


def _column_name(spec: FeatureSpec) -> str:
    if not spec.params:
        return spec.name
    parts = "_".join(f"{k}{v}" for k, v in sorted(spec.params.items()))
    return f"{spec.name}_{parts}"


class FeatureMatrix:
    """Aligned feature columns plus the row mask of fully-warmed positions."""

    __slots__ = ("names", "valid", "values")

    def __init__(self, names: list[str], values: Array) -> None:
        self.names = names
        self.values = values
        # rows where every feature is finite (warmup complete for all features)
        self.valid: NDArray[np.bool_] = np.all(np.isfinite(values), axis=1)

    def warmed(self) -> Array:
        """The matrix restricted to rows where all features are finite."""
        return self.values[self.valid]


def build_features(series: Array, specs: list[FeatureSpec]) -> FeatureMatrix:
    """Compute all configured features into an aligned (T, F) matrix."""
    if not specs:
        raise ValueError("at least one feature is required")
    series = _as_f64(series)
    columns: list[Array] = []
    names: list[str] = []
    for spec in specs:
        fn = _REGISTRY.get(spec.name)
        if fn is None:
            raise KeyError(f"unknown feature {spec.name!r}; available: {available_features()}")
        columns.append(fn(series, **spec.params))
        names.append(_column_name(spec))
    return FeatureMatrix(names, np.column_stack(columns))


# --- leakage canary -----------------------------------------------------------


def is_causal(
    fn: FeatureFn,
    series: Array,
    params: dict[str, object] | None = None,
    *,
    n_probe: int = 60,
    atol: float = 1e-9,
) -> bool:
    """True iff `fn` is causal by the expanding-window-endpoint test.

    For a causal feature, the value at time ``t`` must depend only on data up
    to ``t`` — so computing the feature on the prefix ``series[:t+1]`` and
    taking its *last* element must reproduce the full-series value at ``t``
    (whenever that value is defined, i.e. past the warmup). A forward-peeking
    feature cannot produce that endpoint from a prefix that ends at ``t``: it
    needs data the prefix does not contain, so its endpoint is undefined (or
    wrong) and the check fails.

    This is structural, not statistical: there is no correlation threshold to
    tune. We probe ``n_probe`` positions spread across the series (full O(n^2)
    is unnecessary to expose a leak).
    """
    series = _as_f64(series)
    params = params or {}
    full = fn(series, **params)
    n = series.shape[0]
    if n < 2:
        return True

    probes = np.unique(np.linspace(1, n - 1, num=min(n_probe, n - 1), dtype=np.int64))
    for t in probes:
        ti = int(t)
        full_val = full[ti]
        if not np.isfinite(full_val):
            continue  # warmup: undefined for legitimate (causal) reasons
        endpoint = fn(series[: ti + 1], **params)[ti]
        # A causal feature reproduces full[t] from data up to t. A leaky one
        # either cannot (NaN endpoint) or produces a different value.
        if not np.isfinite(endpoint) or not np.isclose(endpoint, full_val, atol=atol):
            return False
    return True


def assert_causal(fn: FeatureFn, series: Array, params: dict[str, object] | None = None) -> None:
    """Raise ``LeakageError`` if `fn` is not causal on `series`."""
    if not is_causal(fn, series, params):
        raise LeakageError(
            f"feature {getattr(fn, '__name__', fn)!r} is not causal: its past "
            "values change when future data is revealed (look-ahead leakage)"
        )


class LeakageError(AssertionError):
    """Raised when a feature uses information from the future."""
