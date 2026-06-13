"""Statistical-honesty tests: metrics, Deflated Sharpe Ratio, SPA / Reality
Check, and the trial log. The load-bearing tests are the ones showing the
corrections actually bite — a lucky winner among many is NOT declared real."""

import numpy as np
import pytest
from numpy.typing import NDArray

from quantis.evaluation.metrics import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    max_drawdown,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    sortino_ratio,
)
from quantis.evaluation.multiple_testing import spa_test
from quantis.evaluation.trial_log import TrialLog, TrialRecord

Array = NDArray[np.float64]


def test_sharpe_and_sortino_basic() -> None:
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, size=2000)
    sr = sharpe_ratio(r)
    assert 0.05 < sr < 0.15  # ~0.1 per period
    # Sortino >= Sharpe when downside vol <= total vol (symmetric-ish here)
    assert sortino_ratio(r) > 0


def test_max_drawdown_on_known_path() -> None:
    # +10%, then -50% (peak 1.1 -> trough 0.55): drawdown 0.5
    r = np.array([0.10, -0.5, 0.0])
    assert abs(max_drawdown(r) - 0.5) < 1e-9


def test_expected_max_sharpe_grows_with_trials() -> None:
    rng = np.random.default_rng(1)
    few = rng.normal(0, 0.2, size=10)
    many = rng.normal(0, 0.2, size=500)
    assert expected_max_sharpe(many) > expected_max_sharpe(few)


def test_psr_increases_with_sample_length() -> None:
    rng = np.random.default_rng(2)
    short = rng.normal(0.05, 1.0, size=50)
    long = rng.normal(0.05, 1.0, size=5000)
    # same edge, more data => more confident it's positive
    assert probabilistic_sharpe_ratio(long) > probabilistic_sharpe_ratio(short)


def test_deflated_sharpe_punishes_multiple_testing() -> None:
    """The key honesty test. Take the best of many ZERO-edge strategies; its raw
    Sharpe looks positive, but the DSR must reveal it as unconvincing once
    deflated by how many strategies were searched."""
    rng = np.random.default_rng(3)
    n_trials, n_obs = 200, 500
    trials = rng.normal(0.0, 1.0, size=(n_trials, n_obs))  # all zero true edge
    sharpes = np.array([sharpe_ratio(trials[i]) for i in range(n_trials)])
    best = int(np.argmax(sharpes))

    raw_psr = probabilistic_sharpe_ratio(trials[best])  # vs 0 benchmark
    dsr = deflated_sharpe_ratio(trials[best], sharpes)

    # the winner looks good naively...
    assert raw_psr > 0.9
    # ...but deflation against the searched trials strips the illusion
    assert dsr < 0.7, f"DSR {dsr} failed to deflate a lucky winner"


def test_spa_does_not_flag_pure_noise() -> None:
    rng = np.random.default_rng(4)
    # 20 zero-edge strategies; SPA should NOT reject the null (high p-value)
    perf = rng.normal(0.0, 0.01, size=(600, 20))
    result = spa_test(perf, n_bootstrap=500, seed=4)
    assert result.spa_pvalue > 0.10, f"SPA falsely flagged noise (p={result.spa_pvalue})"


def test_spa_detects_a_genuine_edge() -> None:
    rng = np.random.default_rng(5)
    perf = rng.normal(0.0, 0.01, size=(600, 20))
    # inject a real positive edge into one strategy
    perf[:, 7] += 0.004
    result = spa_test(perf, n_bootstrap=500, seed=5)
    assert result.best_strategy == 7
    assert result.spa_pvalue < 0.05, f"SPA missed a real edge (p={result.spa_pvalue})"
    # SPA is at least as powerful as the conservative Reality Check
    assert result.spa_pvalue <= result.reality_check_pvalue + 1e-9


def test_trial_log_roundtrip_and_feeds_dsr(tmp_path: object) -> None:
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    rng = np.random.default_rng(6)
    log = TrialLog(tmp_path / "trials.jsonl")
    for i in range(30):
        log.append(TrialRecord(name=f"trial_{i}", returns=list(rng.normal(0, 1.0, size=300))))

    loaded = log.load()
    assert len(loaded) == 30
    sharpes = log.sharpes()
    assert sharpes.shape == (30,)
    # the matrix aligns and SPA runs end to end off the log
    perf = log.performance_matrix()
    assert perf.shape == (300, 30)
    result = spa_test(perf, n_bootstrap=300, seed=0)
    assert 0.0 <= result.spa_pvalue <= 1.0


def test_trial_log_rejects_misaligned_lengths(tmp_path: object) -> None:
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    log = TrialLog(tmp_path / "trials.jsonl")
    log.append(TrialRecord(name="a", returns=[0.1, 0.2, 0.3]))
    log.append(TrialRecord(name="b", returns=[0.1, 0.2]))
    with pytest.raises(ValueError, match="differing lengths"):
        log.performance_matrix()
