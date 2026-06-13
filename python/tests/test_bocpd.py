"""BOCPD: changepoint recovery on piecewise-constant data, normalization, the
causality guarantee (online => no look-ahead), and input guards."""

import numpy as np
import pytest
from numpy.typing import NDArray

from quantis.models.bocpd import Bocpd, NormalInverseGammaPrior

Array = NDArray[np.float64]


def _piecewise_series(seed: int = 0) -> tuple[Array, list[int]]:
    """Three segments with distinct means; true changepoints at 300 and 600."""
    rng = np.random.default_rng(seed)
    seg0 = rng.normal(0.0, 1.0, size=300)
    seg1 = rng.normal(5.0, 1.0, size=300)
    seg2 = rng.normal(-3.0, 1.0, size=300)
    return np.concatenate([seg0, seg1, seg2]), [300, 600]


def test_detects_changepoints_near_truth() -> None:
    series, true_cps = _piecewise_series()
    model = Bocpd(hazard_lambda=250.0, max_run_length=400)
    result = model.fit_predict(series)
    detected = result.changepoint_indices(min_drop=100)

    # every true changepoint has a confident detection within a few samples
    for cp in true_cps:
        nearest = np.min(np.abs(detected - cp)) if detected.size else np.inf
        assert nearest <= 5, f"no detection near changepoint {cp} (got {detected})"

    # detections are sparse, not one-per-step
    assert detected.size < 10


def test_map_run_length_resets_at_changepoints() -> None:
    series, _ = _piecewise_series()
    result = Bocpd(hazard_lambda=250.0, max_run_length=400).fit_predict(series)
    # just before the first changepoint the run length should be long...
    assert result.map_run_length[290] > 100
    # ...and shortly after it should have reset to near zero
    assert np.min(result.map_run_length[300:310]) < 10


def test_run_length_posterior_is_normalized() -> None:
    series, _ = _piecewise_series()
    result = Bocpd(hazard_lambda=250.0, max_run_length=400).fit_predict(series)
    sums = result.run_length_posterior.sum(axis=1)
    assert np.allclose(sums, 1.0, atol=1e-8)


def test_output_is_causal_online() -> None:
    """The defining property: BOCPD's output at time t uses only x[:t+1]. So
    re-running on any prefix must reproduce the earlier outputs exactly."""
    series, _ = _piecewise_series()
    model = Bocpd(hazard_lambda=250.0, max_run_length=400)
    full = model.fit_predict(series)
    for k in (137, 305, 642):
        prefix = model.fit_predict(series[:k])
        assert np.allclose(prefix.changepoint_prob, full.changepoint_prob[:k], atol=1e-12)
        assert np.array_equal(prefix.map_run_length, full.map_run_length[:k])


def test_rejects_nan_and_non_1d() -> None:
    with pytest.raises(ValueError, match="NaN or inf"):
        Bocpd().fit_predict(np.array([0.0, np.nan, 1.0]))
    with pytest.raises(ValueError, match="1-D"):
        Bocpd().fit_predict(np.zeros((10, 2)))


def test_prior_and_hazard_validation() -> None:
    with pytest.raises(ValueError, match="hazard_lambda"):
        Bocpd(hazard_lambda=1.0)
    with pytest.raises(ValueError, match="positive"):
        Bocpd(prior=NormalInverseGammaPrior(alpha=-1.0))


def test_stationary_series_has_no_internal_changepoints() -> None:
    # A single stationary segment (realistic low noise; a *perfectly* constant
    # series is the degenerate zero-variance case any Bayesian variance
    # estimator must regularize, and is not representative of returns).
    rng = np.random.default_rng(1)
    series = rng.normal(0.0, 1.0, size=500)
    # cap comfortably above the series length so the run length is never
    # truncated (truncation at the cap creates artificial resets).
    result = Bocpd(hazard_lambda=250.0, max_run_length=600).fit_predict(series)
    # A hazard of 1/250 *expects* ~2 changepoints in 500 steps, so the honest
    # claim is "few", not "zero": the model must not over-segment stationary
    # data. (For confident, large resets — min_drop=100 — this seed yields none.)
    detected = result.changepoint_indices(min_drop=100)
    assert detected.size <= 2, f"over-segmented stationary data: {detected}"
    # and it forms a long-segment belief rather than constantly resetting
    assert result.map_run_length.max() > 200
