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
# Fraction of full Kelly (full Kelly is famously too aggressive under parameter
# uncertainty — ADR-005) and the hard leverage cap on the Kelly weight.
DEFAULT_KELLY_FRACTION = 0.25
DEFAULT_KELLY_CAP = 3.0


def _weighted_returns(
    close: Array,
    valid: NDArray[np.int64],
    weight: Array,
    *,
    cost_bps: float,
    funding_daily: Array | None,
) -> RegimeReturns:
    """Turn a causal per-row equity weight into a RegimeReturns, charging cost on
    turnover ``|Δw|`` and funding on the held weight. Shared by every sizer here
    so the accounting is defined once. The weight at row j is held over the
    return into candle valid[j]+1, so the final valid row is dropped."""
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
    return _weighted_returns(close, valid, weight, cost_bps=cost_bps, funding_daily=funding_daily)


def causal_kelly_weight(
    model: GaussianHMM,
    close: Array,
    *,
    fraction: float = DEFAULT_KELLY_FRACTION,
    cap: float = DEFAULT_KELLY_CAP,
    vol_window: int = DEFAULT_VOL_WINDOW,
) -> tuple[Array, NDArray[np.int64]]:
    """Capped fractional Kelly equity weight per warmed row, and its close indices.

    The risk crate's ``capped_fractional_kelly`` (ADR-005) is ``clamp(fraction ·
    edge/variance, ±cap)``. Here the **edge** is the model's own causal expected
    next-bar return — ``filter_proba(x) @ means[:, 0]`` under the fixed fitted
    HMM — and the **variance** is ``realized_vol**2``. No separate bull gate is
    needed: in bear/chop the expected return is ~0 or negative, so the long-only
    floor (clamp at 0) takes the weight to cash on its own. Causal because both
    the filtered posterior and ``realized_vol`` at ``t`` use only data through
    ``t`` (prefix-invariance asserted in ``tests/test_sizing.py``).
    """
    close = np.asarray(close, dtype=np.float64)
    fm = regime_features(close, vol_window)
    valid = np.flatnonzero(fm.valid)
    x = fm.values[valid]
    edge = model.filter_proba(x) @ model.means[:, 0]  # expected next-bar return
    variance = realized_vol(close, window=vol_window)[valid] ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = np.where(variance > 0.0, fraction * edge / variance, 0.0)
    weight: Array = np.clip(raw, 0.0, cap)  # long-only: clamp at the floor, not -cap
    return weight, valid


def causal_kelly_returns(
    model: GaussianHMM,
    close: Array,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    vol_window: int = DEFAULT_VOL_WINDOW,
    funding_daily: Array | None = None,
    fraction: float = DEFAULT_KELLY_FRACTION,
    cap: float = DEFAULT_KELLY_CAP,
) -> RegimeReturns:
    """Causal regime strategy sized by capped fractional Kelly (long-only).

    Drop-in compatible with the walk-forward harness (extra knobs default). See
    :func:`causal_kelly_weight` for the sizing and its causality argument."""
    close = np.asarray(close, dtype=np.float64)
    weight, valid = causal_kelly_weight(
        model, close, fraction=fraction, cap=cap, vol_window=vol_window
    )
    return _weighted_returns(close, valid, weight, cost_bps=cost_bps, funding_daily=funding_daily)
