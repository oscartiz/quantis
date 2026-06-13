"""The causal regime strategy, shared by the dashboard, the holdout, and the
walk-forward harness so all three evaluate the *same* rule.

The rule: fit a 3-state Gaussian HMM on (log-return, realized-vol) features;
hold long while the **filtered** (causal) regime is bull, flat otherwise. The
position decided after observing bar ``t`` is held over bar ``t+1`` (no
look-ahead), and a round-trip cost is charged whenever the position changes.

Smoothed regimes are *not* used here — they are non-causal and belong only to
the dashboard's analysis overlay (ADR-003).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from quantis.config import FeatureSpec
from quantis.features import FeatureMatrix, build_features
from quantis.models.hmm import GaussianHMM

Array = NDArray[np.float64]

DEFAULT_VOL_WINDOW = 20
DEFAULT_COST_BPS = 5.0
_BULL = 2  # regime rank after ordering: 0=bear, 1=chop, 2=bull


@dataclass(frozen=True)
class RegimeReturns:
    """Realized series from one causal-strategy evaluation."""

    strat: Array  # strategy log returns
    hold: Array  # buy-and-hold log returns over the same bars
    position: Array  # 0/1 position held into each bar
    candle_index: NDArray[np.int64]  # index into the close series per row


def regime_features(close: Array, vol_window: int = DEFAULT_VOL_WINDOW) -> FeatureMatrix:
    """The (log-return, realized-vol) feature matrix the regime model consumes."""
    return build_features(
        close,
        [
            FeatureSpec(name="log_return", params={"lag": 1}),
            FeatureSpec(name="realized_vol", params={"window": vol_window}),
        ],
    )


def ordered_regimes(model: GaussianHMM, proba: Array) -> NDArray[np.int64]:
    """MAP regime per row, relabelled 0=bear,1=chop,2=bull via mean ordering."""
    rank = {int(s): r for r, s in enumerate(model.regime_order())}
    return np.array([rank[int(s)] for s in np.argmax(proba, axis=1)], dtype=np.int64)


def fit_regime_hmm(
    close: Array,
    *,
    seed: int = 42,
    vol_window: int = DEFAULT_VOL_WINDOW,
    n_states: int = 3,
) -> GaussianHMM:
    """Fit the regime HMM on the warmed features of a close-price series."""
    fm = regime_features(close, vol_window)
    x = fm.values[np.flatnonzero(fm.valid)]
    return GaussianHMM(n_states=n_states, seed=seed, n_iter=200, tol=1e-5).fit(x)


def causal_regime_returns(
    model: GaussianHMM,
    close: Array,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    vol_window: int = DEFAULT_VOL_WINDOW,
) -> RegimeReturns:
    """Evaluate the causal strategy on ``close`` under a fixed (already-fit)
    model. Uses only filtered (causal) regimes, so it contains no look-ahead."""
    fm = regime_features(close, vol_window)
    valid = np.flatnonzero(fm.valid)
    x = fm.values[valid]
    filtered = ordered_regimes(model, model.filter_proba(x))
    target = (filtered == _BULL).astype(np.float64)

    # The position at row j is held over the return into candle valid[j]+1, so
    # the final valid row (no next candle) is dropped.
    n = len(valid) - 1 if valid[-1] + 1 >= len(close) else len(valid)
    idx = valid[:n]
    next_ret = np.log(close[idx + 1] / close[idx])
    position = target[:n]
    prev = np.concatenate([[0.0], position[:-1]])
    cost = np.abs(position - prev) * (cost_bps / 10_000.0)
    return RegimeReturns(
        strat=position * next_ret - cost,
        hold=next_ret,
        position=position,
        candle_index=idx,
    )
