"""HMM + BOCPD ensemble: a fast, causal, risk-off overlay on the regime filter.

The Gaussian HMM filter (:mod:`quantis.evaluation.regime_strategy`) is a slow,
smooth *directional* classifier: it decides bull-vs-not from a few recurring
states, and is structurally late to leave a bull regime once one breaks down (a
filtered posterior migrates state-by-state). BOCPD
(:mod:`quantis.models.bocpd`) is the project's *causal* counterpart: it detects,
online, that the return-generating distribution just broke — but it cannot label
the new segment's direction.

This module composes them respecting exactly what each can honestly say:

* the **HMM** decides *direction* — go long only in a confirmed bull regime;
* **BOCPD** decides *stability* — it can only say "the distribution just
  changed", never "bull"/"bear", so it is used **one-directionally, to
  de-risk**: while a fresh segment has not yet persisted (MAP run length below
  ``min_run_length``) the overlay forces the position flat even if the HMM still
  reads bull. It can subtract exposure, never add it.

That is the project's identified edge — a drawdown-avoider (see the README) —
sharpened: BOCPD can step aside at the *start* of a break, before the filtered
HMM posterior has migrated out of the bull state. The cost is being even later
to re-enter a new bull (every segment starts "unconfirmed"). Whether the trade
is worth it is an empirical question answered honestly by
``scripts/ensemble_eval.py`` (walk-forward distribution + Deflated-Sharpe / SPA),
not asserted here. ADR-007 records the decision.

Causality. Everything BOCPD sees at row ``t`` is a function of ``close[:t+1]``
only: the inputs are 1-step log returns standardized by a rolling volatility
computed from *strictly prior* bars (shift by one), so the scale of ``r_t``
never uses ``r_t`` itself. BOCPD is causal by construction (its run-length
posterior at ``t`` uses only ``x[:t+1]``; asserted in ``tests/test_bocpd.py``),
and ``tests/test_ensemble.py`` re-asserts the whole overlay's prefix-invariance.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from quantis.evaluation.regime_strategy import (
    DEFAULT_COST_BPS,
    DEFAULT_VOL_WINDOW,
    RegimeReturns,
    ordered_regimes,
    regime_features,
)
from quantis.features.pipeline import log_return, realized_vol
from quantis.models.bocpd import Bocpd, NormalInverseGammaPrior
from quantis.models.hmm import GaussianHMM

Array = NDArray[np.float64]

# Expected segment length (days) for the BOCPD hazard. Shorter than the model's
# 250 default: regime *breaks* are what we want to catch quickly, not long-run
# stationary segments.
DEFAULT_HAZARD_LAMBDA = 100.0
# Bars a fresh segment must persist before the overlay trusts the HMM long again.
DEFAULT_MIN_RUN_LENGTH = 5


def _vol_standardized_returns(close: Array, vol_window: int) -> Array:
    """1-step log returns scaled by a *prior-bar* rolling volatility.

    The scale for ``r_t`` is the realized volatility over the window ending at
    ``t-1`` (a one-bar shift), so it uses only returns strictly before ``t``.
    The result is ~unit variance, which matches BOCPD's default
    Normal-Inverse-Gamma prior without any hand-tuned magic scale constant, and
    turns an abrupt level/volatility break into a large standardized outlier the
    detector reacts to. Warmup positions are ``NaN`` (never forward-filled).
    """
    rets = log_return(close, lag=1)
    vol = realized_vol(close, window=vol_window)
    scale = np.full_like(vol, np.nan)
    scale[1:] = vol[:-1]  # scale for r_t = volatility measured through t-1
    with np.errstate(invalid="ignore", divide="ignore"):
        z: Array = rets / scale
    return z


def causal_run_length(
    close: Array,
    *,
    vol_window: int = DEFAULT_VOL_WINDOW,
    hazard_lambda: float = DEFAULT_HAZARD_LAMBDA,
    prior: NormalInverseGammaPrior | None = None,
) -> NDArray[np.int64]:
    """BOCPD MAP run length aligned to ``close`` indices.

    Returns an int array the length of ``close``. Warmup rows (before the
    standardized-return series is defined) are filled with the int64 max, so a
    ``run_length >= min_run_length`` test reads them as 'stable' — the overlay
    never forces flat purely for lack of a changepoint signal.

    Causal: row ``t``'s value depends only on ``close[:t+1]`` (BOCPD is
    prefix-invariant, so the value is identical whether computed on the prefix
    or on the whole series — see ``tests/test_ensemble.py``).
    """
    close = np.asarray(close, dtype=np.float64)
    z = _vol_standardized_returns(close, vol_window)
    out = np.full(close.shape[0], np.iinfo(np.int64).max, dtype=np.int64)
    finite = np.flatnonzero(np.isfinite(z))
    if finite.size == 0:
        return out
    first = int(finite[0])
    seq = z[first:]
    if not np.all(np.isfinite(seq)):
        # The candle loader forbids gaps, so the standardized series is
        # contiguous past warmup; assert it rather than silently mis-aligning.
        raise ValueError("standardized return series has interior non-finite values")
    result = Bocpd(hazard_lambda=hazard_lambda, prior=prior).fit_predict(seq)
    out[first:] = result.map_run_length
    return out


def causal_ensemble_returns(
    model: GaussianHMM,
    close: Array,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    vol_window: int = DEFAULT_VOL_WINDOW,
    funding_daily: Array | None = None,
    bull_rank: int = 2,
    hazard_lambda: float = DEFAULT_HAZARD_LAMBDA,
    min_run_length: int = DEFAULT_MIN_RUN_LENGTH,
    run_length: NDArray[np.int64] | None = None,
) -> RegimeReturns:
    """Causal HMM-bull strategy with a BOCPD risk-off overlay.

    Identical to :func:`quantis.evaluation.regime_strategy.causal_regime_returns`
    — same features, same filtered (causal) HMM signal, same costs and funding
    convention — except the long position is additionally gated off while
    BOCPD's MAP run length is below ``min_run_length`` (the current segment has
    not yet persisted). The overlay only ever turns long → flat, so the ensemble
    position is ``<=`` the HMM position on every bar.

    ``run_length`` may be supplied precomputed (aligned to ``close``); otherwise
    it is computed here. Signature is drop-in compatible with
    ``causal_regime_returns`` for the walk-forward harness (extra knobs default).
    """
    close = np.asarray(close, dtype=np.float64)
    fm = regime_features(close, vol_window)
    valid = np.flatnonzero(fm.valid)
    x = fm.values[valid]
    filtered = ordered_regimes(model, model.filter_proba(x))
    hmm_long = (filtered == bull_rank).astype(np.float64)

    if run_length is None:
        run_length = causal_run_length(close, vol_window=vol_window, hazard_lambda=hazard_lambda)
    confirmed = (run_length[valid] >= min_run_length).astype(np.float64)
    target = hmm_long * confirmed

    # The position at row j is held over the return into candle valid[j]+1, so
    # the final valid row (no next candle) is dropped — identical to the HMM path.
    n = len(valid) - 1 if valid[-1] + 1 >= len(close) else len(valid)
    idx = valid[:n]
    next_ret = np.log(close[idx + 1] / close[idx])
    position = target[:n]
    prev = np.concatenate([[0.0], position[:-1]])
    cost = np.abs(position - prev) * (cost_bps / 10_000.0)
    funding = position * funding_daily[idx + 1] if funding_daily is not None else 0.0
    return RegimeReturns(
        strat=position * next_ret - cost - funding,
        hold=next_ret,
        position=position,
        candle_index=idx,
    )
