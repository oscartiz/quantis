"""Funding: cadence-agnostic daily aggregation, and a one-sided drag on longs.

The netting must be exact (integer-clean fractions) and must never touch a flat
position, so the gross result is recoverable by zeroing funding — the same
default-off guarantee that keeps the committed numbers unchanged.
"""

from pathlib import Path

import numpy as np

from quantis.data.candles import load_candles
from quantis.data.funding import load_daily_funding
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDLES = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"
_FUNDING = _REPO_ROOT / "data" / "sample" / "btc-funding.csv"

_JAN1 = 1672531200000  # 2023-01-01 00:00 UTC
_HOUR = 3600000


def test_load_daily_funding_sums_per_day_and_aligns(tmp_path: Path) -> None:
    csv = tmp_path / "f.csv"
    # two events on Jan-01 (8h apart), one on Jan-02; Jan-03 has none.
    csv.write_text(
        "time_ms,funding_rate\n"
        f"{_JAN1},0.0001\n"
        f"{_JAN1 + 8 * _HOUR},0.0002\n"
        f"{_JAN1 + 24 * _HOUR},0.0005\n",
        encoding="utf-8",
    )
    out = load_daily_funding(csv, ["2023-01-01", "2023-01-02", "2023-01-03"])
    assert np.allclose(out, [0.0003, 0.0005, 0.0])  # summed, aligned, zero-filled


def test_load_daily_funding_real_file_is_one_sided() -> None:
    candles = load_candles(_CANDLES)
    f = load_daily_funding(_FUNDING, candles.dates_iso())
    assert f.shape[0] == len(candles)
    covered = f != 0.0
    assert covered.sum() > 1000  # ~1129 covered days
    assert (f[covered] > 0).mean() > 0.5  # longs pay most days


def test_funding_only_drags_long_days_and_zero_recovers_gross() -> None:
    candles = load_candles(_CANDLES)
    f = load_daily_funding(_FUNDING, candles.dates_iso())
    model = fit_regime_hmm(candles.close, seed=42)

    gross = causal_regime_returns(model, candles.close)
    net = causal_regime_returns(model, candles.close, funding_daily=f)

    # net = gross - position * funding(next bar), exactly
    drag = gross.position * f[gross.candle_index + 1]
    assert np.allclose(gross.strat - net.strat, drag)
    # flat days are untouched
    flat = gross.position == 0
    assert np.array_equal(net.strat[flat], gross.strat[flat])
    # zero funding recovers the gross (default-off guarantee)
    zero = causal_regime_returns(model, candles.close, funding_daily=np.zeros_like(f))
    assert np.array_equal(zero.strat, gross.strat)


def test_bull_rank_defaults_unchanged_and_threads_through() -> None:
    candles = load_candles(_CANDLES)
    model = fit_regime_hmm(candles.close, seed=42)
    default = causal_regime_returns(model, candles.close)
    explicit = causal_regime_returns(model, candles.close, bull_rank=2)
    assert np.array_equal(default.strat, explicit.strat)

    # a rank no regime can take leaves the strategy permanently flat
    flat = causal_regime_returns(model, candles.close, bull_rank=99)
    assert flat.position.sum() == 0
    assert np.allclose(flat.strat, 0.0)
