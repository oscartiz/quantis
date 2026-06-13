"""Walk-forward out-of-sample evaluation of the regime strategy.

The single sealed holdout (`scripts/evaluate_holdout.py`) is one out-of-sample
draw — and a favourable one, since its window was a bear market the strategy is
built to sidestep. This harness turns that N = 1 into a *distribution*: it walks
the series forward, and at each step refits the regime model on everything up to
that point and evaluates the causal strategy on the following window, using only
data the model has not been fit on. The honest summary statistic is the spread
of those out-of-sample results, not any single window.

No look-ahead by construction: the model at each step is fit strictly on the
past, and the causal features at test points may legitimately use past returns
to warm up (that is what a live system does) — only model *fitting* is walled
off to the training span.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from quantis.evaluation.metrics import max_drawdown, sharpe_ratio
from quantis.evaluation.regime_strategy import (
    DEFAULT_COST_BPS,
    DEFAULT_VOL_WINDOW,
    causal_regime_returns,
    fit_regime_hmm,
)

Array = NDArray[np.float64]


@dataclass(frozen=True)
class WindowResult:
    """One walk-forward window's out-of-sample outcome."""

    train_end_index: int
    n_oos: int
    strat_sharpe: float
    strat_total_return: float
    strat_max_drawdown: float
    hold_sharpe: float
    hold_total_return: float
    time_in_market: float


@dataclass(frozen=True)
class WalkForwardResult:
    """Aggregate distribution across all walk-forward windows."""

    windows: list[WindowResult]
    n_windows: int
    mean_strat_sharpe: float
    median_strat_sharpe: float
    std_strat_sharpe: float
    frac_positive_return: float
    frac_beat_hold_sharpe: float
    mean_time_in_market: float
    # Sharpe of all OOS returns concatenated — "trading every window in turn".
    pooled_strat_sharpe: float
    pooled_hold_sharpe: float
    pooled_strat_total_return: float
    pooled_hold_total_return: float


def walk_forward_evaluate(
    close: Array,
    *,
    train_min: int,
    test_window: int,
    step: int,
    seed: int = 42,
    periods_per_year: float = 365.0,
    cost_bps: float = DEFAULT_COST_BPS,
    vol_window: int = DEFAULT_VOL_WINDOW,
    min_oos: int = 10,
) -> WalkForwardResult:
    """Evaluate the regime strategy walk-forward over ``close``.

    At each ``train_end`` (from ``train_min``, stepping by ``step``), the model
    is fit on ``close[:train_end]`` and evaluated on the next ``test_window``
    bars. Returns per-window results and their aggregate distribution.
    """
    if train_min <= vol_window + 2:
        raise ValueError("train_min must exceed the feature warmup")
    if test_window < min_oos or step < 1:
        raise ValueError("test_window must be >= min_oos and step >= 1")

    n = close.shape[0]
    warmup = vol_window + 2  # enough prior bars to warm the features at test start
    windows: list[WindowResult] = []
    pooled_strat: list[Array] = []
    pooled_hold: list[Array] = []

    train_end = train_min
    while train_end < n - 1:
        test_end = min(train_end + test_window, n)
        # Fit strictly on the past.
        model = fit_regime_hmm(close[:train_end], seed=seed, vol_window=vol_window)
        # Evaluate on a slice that borrows `warmup` pre-test bars so the causal
        # features are warm at the test boundary; keep only the OOS rows.
        slice_start = max(0, train_end - warmup)
        sliced = close[slice_start:test_end]
        r = causal_regime_returns(model, sliced, cost_bps=cost_bps, vol_window=vol_window)
        global_index = slice_start + r.candle_index
        oos = global_index >= train_end

        n_oos = int(np.sum(oos))
        if n_oos >= min_oos:
            strat = r.strat[oos]
            hold = r.hold[oos]
            pos = r.position[oos]
            pooled_strat.append(strat)
            pooled_hold.append(hold)
            windows.append(
                WindowResult(
                    train_end_index=train_end,
                    n_oos=n_oos,
                    strat_sharpe=sharpe_ratio(strat, periods_per_year),
                    strat_total_return=float(np.exp(np.sum(strat)) - 1.0),
                    strat_max_drawdown=max_drawdown(strat),
                    hold_sharpe=sharpe_ratio(hold, periods_per_year),
                    hold_total_return=float(np.exp(np.sum(hold)) - 1.0),
                    time_in_market=float(np.mean(pos)),
                )
            )
        train_end += step

    if not windows:
        raise ValueError("no walk-forward windows produced; check the parameters")

    strat_sharpes = np.array([w.strat_sharpe for w in windows])
    all_strat = np.concatenate(pooled_strat)
    all_hold = np.concatenate(pooled_hold)
    return WalkForwardResult(
        windows=windows,
        n_windows=len(windows),
        mean_strat_sharpe=float(np.mean(strat_sharpes)),
        median_strat_sharpe=float(np.median(strat_sharpes)),
        std_strat_sharpe=float(np.std(strat_sharpes, ddof=1)) if len(windows) > 1 else 0.0,
        frac_positive_return=float(np.mean([w.strat_total_return > 0 for w in windows])),
        frac_beat_hold_sharpe=float(np.mean([w.strat_sharpe > w.hold_sharpe for w in windows])),
        mean_time_in_market=float(np.mean([w.time_in_market for w in windows])),
        pooled_strat_sharpe=sharpe_ratio(all_strat, periods_per_year),
        pooled_hold_sharpe=sharpe_ratio(all_hold, periods_per_year),
        pooled_strat_total_return=float(np.exp(np.sum(all_strat)) - 1.0),
        pooled_hold_total_return=float(np.exp(np.sum(all_hold)) - 1.0),
    )
