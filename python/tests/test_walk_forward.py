"""Walk-forward harness: deterministic, window-respecting, and honest about
its own distribution (no single-window cherry-pick).

A coarse config (few windows) keeps the suite fast — each window refits an HMM,
so the full quarterly sweep is reserved for scripts/walk_forward_eval.py."""

from pathlib import Path

import numpy as np
import pytest

from quantis.data.candles import load_candles
from quantis.evaluation.walk_forward import WalkForwardResult, walk_forward_evaluate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDLES = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"

# Coarse on purpose: ~4-5 windows instead of ~20, to keep refits cheap.
_CFG = {"train_min": 500, "test_window": 120, "step": 150, "seed": 42}


def _run(close: np.ndarray) -> WalkForwardResult:
    return walk_forward_evaluate(close, train_min=500, test_window=120, step=150, seed=42)


@pytest.fixture(scope="module")
def result() -> WalkForwardResult:
    close = load_candles(_CANDLES).close
    return _run(close)


def test_produces_multiple_windows_within_bounds(result: WalkForwardResult) -> None:
    assert result.n_windows >= 3
    assert all(0 < w.n_oos <= _CFG["test_window"] for w in result.windows)
    ends = [w.train_end_index for w in result.windows]
    assert ends == sorted(ends)
    assert ends[0] >= _CFG["train_min"]


def test_is_deterministic() -> None:
    close = load_candles(_CANDLES).close
    a = _run(close)
    b = _run(close)
    assert a.n_windows == b.n_windows
    assert a.pooled_strat_sharpe == b.pooled_strat_sharpe
    assert [w.strat_total_return for w in a.windows] == [w.strat_total_return for w in b.windows]


def test_distribution_fields_are_consistent(result: WalkForwardResult) -> None:
    assert 0.0 <= result.frac_positive_return <= 1.0
    assert 0.0 <= result.frac_beat_hold_sharpe <= 1.0
    assert 0.0 <= result.mean_time_in_market <= 1.0
    sharpes = sorted(w.strat_sharpe for w in result.windows)
    assert abs(result.median_strat_sharpe - float(np.median(sharpes))) < 1e-9
    for v in (result.mean_strat_sharpe, result.std_strat_sharpe, result.pooled_strat_sharpe):
        assert np.isfinite(v)


def test_rejects_degenerate_parameters() -> None:
    close = load_candles(_CANDLES).close
    with pytest.raises(ValueError, match="train_min"):
        walk_forward_evaluate(close, train_min=5, test_window=90, step=45)
    with pytest.raises(ValueError, match="min_oos"):
        walk_forward_evaluate(close, train_min=365, test_window=3, step=45, min_oos=10)
