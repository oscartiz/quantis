"""Continuous position sizing for the regime strategy: volatility targeting and
optional conviction weighting, as a causal research-layer port of the Rust risk
crate's advisory sizer (`crates/risk/src/sizing.rs`, ADR-005).

The HMM filter and its BOCPD overlay both trade a **binary** long/flat book: in
a bull regime hold one unit, otherwise hold cash. That throws away two pieces of
information the model already produces:

* **how volatile the market is right now** — a fixed unit position takes far more
  risk in a 6%/day tape than a 1%/day one; and
* **how confident the regime call is** — the filtered posterior ``P(bull)`` is a
  probability, not a coin flip, yet the binary rule discards everything between 0
  and 1.

This module sizes the position as an **equity weight** instead, faithfully to the
risk crate (which the execution path already uses, but the research never did):

* **Volatility targeting** — ``w = clamp(target_vol / realized_vol, 0,
  max_leverage)``. This is exactly ``crate::risk::vol_target_qty`` expressed as a
  fraction of equity (``notional = equity · target_vol/realized_vol`` capped at
  ``max_leverage · equity``): hold less when the market is wild, more when it is
  calm, and never more than the leverage cap even as ``realized_vol → 0``.
* **Conviction weighting** (optional) — scale the weight by ``P(bull)`` (the
  filtered posterior of the bull state) rather than the hard ``argmax`` regime,
  so a 0.9-confidence bull is held larger than a 0.55 one.

The position weight is ``signal · size`` with ``signal`` either the binary bull
indicator or ``P(bull)``, and ``size`` either 1 (``target_vol=None``, the binary
baseline) or the vol-target clamp. Costs are charged on **turnover** ``|Δw|`` (a
continuous generalization of the binary round-trip), and funding accrues on the
held weight. With ``target_vol=None`` and binary signal the result is **identical**
to :func:`quantis.evaluation.regime_strategy.causal_regime_returns` (tested), so
this is a strict generalization whose only new behaviour is the sizing.

Causality. ``realized_vol`` and ``P(bull)`` at row ``t`` use only data up to ``t``
(the feature pipeline is causal by construction; ``filter_proba`` is the
forward-only HMM posterior, ADR-003). The weight that is *held into* bar ``t+1``
is therefore decided with information available at the close of bar ``t`` — no
look-ahead. ``tests/test_sizing.py`` re-asserts the whole strategy's
prefix-invariance under a fixed model.

This is sizing *research*, not the live authority: the integer ``RiskGate``
(ADR-005) remains the only thing that can veto an order. A bad weight here is not
dangerous, by the same advisory/authority split the risk crate is built around.
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
from quantis.features.pipeline import realized_vol
from quantis.models.hmm import GaussianHMM

Array = NDArray[np.float64]

# Per-bar (daily) target volatility, same units as ``realized_vol``. 2%/day ≈
# 38%/yr — a deliberately mild target for a long-only book.
DEFAULT_TARGET_VOL = 0.02
# Hard cap on the equity weight, mirroring the risk crate's leverage cap so a
# collapsing volatility estimate cannot demand an unbounded position.
DEFAULT_MAX_LEVERAGE = 3.0


def causal_position_weight(
    model: GaussianHMM,
    close: Array,
    *,
    target_vol: float | None = None,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
    vol_window: int = DEFAULT_VOL_WINDOW,
    bull_rank: int = 2,
    conviction_weighted: bool = False,
) -> tuple[Array, NDArray[np.int64]]:
    """The causal equity weight per warmed row and the close indices it aligns to.

    Returns ``(weight, valid_index)`` where ``weight[j]`` is the fraction of
    equity to hold over the bar starting at ``close[valid_index[j]]``. The weight
    is in ``[0, max_leverage]`` (long-only). See the module docstring for the
    sizing definition.
    """
    close = np.asarray(close, dtype=np.float64)
    fm = regime_features(close, vol_window)
    valid = np.flatnonzero(fm.valid)
    x = fm.values[valid]

    proba = model.filter_proba(x)
    if conviction_weighted:
        bull_state = int(model.regime_order()[bull_rank])
        signal = proba[:, bull_state]  # P(bull) in [0, 1]
    else:
        signal = (ordered_regimes(model, proba) == bull_rank).astype(np.float64)

    if target_vol is None:
        size = np.ones_like(signal)
    else:
        rv = realized_vol(close, window=vol_window)[valid]
        with np.errstate(invalid="ignore", divide="ignore"):
            raw = np.where(rv > 0.0, target_vol / rv, 0.0)
        size = np.clip(raw, 0.0, max_leverage)

    return signal * size, valid


def causal_sized_returns(
    model: GaussianHMM,
    close: Array,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    vol_window: int = DEFAULT_VOL_WINDOW,
    funding_daily: Array | None = None,
    bull_rank: int = 2,
    target_vol: float | None = None,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
    conviction_weighted: bool = False,
) -> RegimeReturns:
    """Causal regime strategy with continuous volatility-targeted sizing.

    Generalizes :func:`quantis.evaluation.regime_strategy.causal_regime_returns`
    from a 0/1 position to an equity weight (see the module docstring). Costs are
    charged on turnover ``|Δw|`` and funding accrues on the held weight. With
    ``target_vol=None`` and ``conviction_weighted=False`` it reproduces the binary
    strategy exactly. Signature is drop-in compatible with the walk-forward
    harness (extra knobs default).

    The returned :class:`RegimeReturns` ``position`` field carries the *weight*
    (so ``mean(position)`` reads as mean exposure, not fraction of time in
    market); for a binary run the two coincide.
    """
    close = np.asarray(close, dtype=np.float64)
    weight, valid = causal_position_weight(
        model,
        close,
        target_vol=target_vol,
        max_leverage=max_leverage,
        vol_window=vol_window,
        bull_rank=bull_rank,
        conviction_weighted=conviction_weighted,
    )

    # The weight at row j is held over the return into candle valid[j]+1, so the
    # final valid row (no next candle) is dropped — identical to the HMM path.
    n = len(valid) - 1 if valid[-1] + 1 >= len(close) else len(valid)
    idx = valid[:n]
    next_ret = np.log(close[idx + 1] / close[idx])
    position = weight[:n]
    prev = np.concatenate([[0.0], position[:-1]])
    cost = np.abs(position - prev) * (cost_bps / 10_000.0)
    funding = position * funding_daily[idx + 1] if funding_daily is not None else 0.0
    return RegimeReturns(
        strat=position * next_ret - cost - funding,
        hold=next_ret,
        position=position,
        candle_index=idx,
    )
