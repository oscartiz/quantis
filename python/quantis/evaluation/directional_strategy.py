"""Long/short regime trading: trade the *bear* regime short, net of funding.

Every strategy so far is long/flat — long in a confirmed bull regime, cash
otherwise. That leaves the model's bear call unused: the HMM identifies a
low/negative-mean state, and a long-only book can only step aside from it, never
profit from it. This module lets the strategy take the symmetric position:

* ``+1`` (long) in the bull regime,
* ``-1`` (short) in the bear regime when ``short_bear`` is set,
* ``0`` (flat) in chop.

Two honest reasons this is worth a separate, carefully-bounded experiment rather
than the default:

* **Funding flips sign for a short.** A long-only perp book pays funding; a short
  *receives* it when the rate is positive. The existing accounting already
  handles this — funding is charged as ``position · funding_daily`` and a short
  has ``position < 0`` — so shorting a bear during the typically-positive-funding
  regimes is a genuine, correctly-priced tailwind, not a modelling artefact.
* **Shorting adds tail risk** a long/flat book does not have (unbounded loss on a
  squeeze), so whether it pays *after* funding and the multiple-testing
  correction is exactly the kind of question the repo answers empirically
  (``scripts/short_eval.py``), not by assumption.

With ``short_bear=False`` this reduces **exactly** to
:func:`quantis.evaluation.regime_strategy.causal_regime_returns` (tested), so it
is a strict generalization. Causality is unchanged: it uses only the filtered
(causal) regime, the position decided after bar ``t`` is held over ``t+1``, and a
round-trip cost is charged on turnover ``|Δposition|`` (a bull→bear flip costs
two legs). ADR-010 records the decision.
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
from quantis.models.hmm import GaussianHMM

Array = NDArray[np.float64]


def causal_long_short_returns(
    model: GaussianHMM,
    close: Array,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    vol_window: int = DEFAULT_VOL_WINDOW,
    funding_daily: Array | None = None,
    bull_rank: int = 2,
    bear_rank: int = 0,
    short_bear: bool = True,
) -> RegimeReturns:
    """Causal long/short regime strategy.

    Long the bull regime, short the bear regime (if ``short_bear``), flat in chop.
    Identical to :func:`causal_regime_returns` when ``short_bear=False``. Funding
    is charged on the signed position (a short receives positive funding), and
    turnover cost on ``|Δposition|``. Drop-in compatible with the walk-forward
    harness (extra knobs default).
    """
    close = np.asarray(close, dtype=np.float64)
    fm = regime_features(close, vol_window)
    valid = np.flatnonzero(fm.valid)
    x = fm.values[valid]
    ranks = ordered_regimes(model, model.filter_proba(x))

    target = np.where(ranks == bull_rank, 1.0, 0.0)
    if short_bear:
        target = np.where(ranks == bear_rank, -1.0, target)

    # The position at row j is held over the return into candle valid[j]+1, so the
    # final valid row (no next candle) is dropped — identical to the HMM path.
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
