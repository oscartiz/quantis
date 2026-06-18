"""CSCV / PBO: a search over pure noise is maximally overfit (PBO ~ 0.5); a
search containing one genuinely superior config is robust (PBO ~ 0)."""

import numpy as np
import pytest

from quantis.evaluation.cscv import cscv_pbo


def test_pure_noise_search_is_overfit() -> None:
    """N independent zero-edge return streams: selecting the max IS Sharpe is
    selection bias, so the winner regresses OOS *below* median more than half the
    time -> PBO well above 0.5. That high reading is the diagnostic working."""
    rng = np.random.default_rng(0)
    perf = rng.standard_normal((600, 20)) * 0.01
    result = cscv_pbo(perf, n_blocks=10)
    assert result.pbo > 0.5
    assert float(np.median(result.logits)) < 0.0  # IS winner is OOS-below-median


def test_one_genuinely_superior_config_is_robust() -> None:
    """One config has a persistent positive mean; it wins IS and stays best OOS,
    so the IS-best is rarely OOS-below-median -> low PBO."""
    rng = np.random.default_rng(1)
    perf = rng.standard_normal((600, 20)) * 0.01
    perf[:, 7] += 0.01  # a real, persistent edge in one column
    result = cscv_pbo(perf, n_blocks=10)
    assert result.pbo < 0.1
    assert result.median_oos_sharpe_selected > result.median_is_sharpe_selected * 0.5


def test_deterministic_and_split_count() -> None:
    rng = np.random.default_rng(2)
    perf = rng.standard_normal((500, 8)) * 0.01
    a = cscv_pbo(perf, n_blocks=8)
    b = cscv_pbo(perf, n_blocks=8)
    assert a.pbo == b.pbo
    assert a.n_splits == 70  # C(8, 4)
    assert np.array_equal(a.logits, b.logits)


def test_rejects_degenerate_inputs() -> None:
    with pytest.raises(ValueError, match="2-D"):
        cscv_pbo(np.zeros(10), n_blocks=10)
    with pytest.raises(ValueError, match="2 config"):
        cscv_pbo(np.zeros((100, 1)), n_blocks=10)
    with pytest.raises(ValueError, match="even"):
        cscv_pbo(np.zeros((100, 5)), n_blocks=7)
    with pytest.raises(ValueError, match="too few"):
        cscv_pbo(np.zeros((10, 5)), n_blocks=10)
