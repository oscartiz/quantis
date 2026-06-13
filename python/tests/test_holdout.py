"""Holdout wall: chronological sealing, research/holdout separation, the
evaluate-once gate, and tamper detection."""

import numpy as np
import pytest
from numpy.typing import NDArray

from quantis.data.holdout import (
    ACKNOWLEDGEMENT,
    HoldoutManifest,
    HoldoutSealed,
    build_manifest,
    load_research,
    reveal_holdout,
)

Array = NDArray[np.float64]


@pytest.fixture
def series() -> Array:
    return np.arange(1000, dtype=np.float64)


def test_seal_splits_chronologically(series: Array) -> None:
    m = build_manifest(series, holdout_fraction=0.2, source="test")
    assert m.n_total == 1000
    assert m.boundary_index == 800
    assert m.n_research == 800
    assert m.n_holdout == 200
    # research is the past, holdout the future — disjoint and contiguous
    research = load_research(series, m)
    assert research[-1] < series[m.boundary_index]


def test_research_partition_excludes_holdout(series: Array) -> None:
    m = build_manifest(series, holdout_fraction=0.2, source="test")
    research = load_research(series, m)
    assert research.shape[0] == 800
    assert np.array_equal(research, series[:800])


def test_reveal_requires_acknowledgement(series: Array) -> None:
    m = build_manifest(series, holdout_fraction=0.2, source="test")
    with pytest.raises(HoldoutSealed, match="exactly once"):
        reveal_holdout(series, m, acknowledgement="just let me peek")
    revealed = reveal_holdout(series, m, ACKNOWLEDGEMENT)
    assert np.array_equal(revealed, series[800:])


def test_tampered_holdout_is_detected(series: Array) -> None:
    m = build_manifest(series, holdout_fraction=0.2, source="test")
    tampered = series.copy()
    tampered[900] = -999.0  # change a value inside the holdout
    with pytest.raises(HoldoutSealed, match="hash mismatch"):
        reveal_holdout(tampered, m, ACKNOWLEDGEMENT)


def test_changed_length_is_detected(series: Array) -> None:
    m = build_manifest(series, holdout_fraction=0.2, source="test")
    longer = np.arange(1100, dtype=np.float64)
    with pytest.raises(HoldoutSealed, match="length"):
        load_research(longer, m)


def test_manifest_roundtrips_json(series: Array, tmp_path: object) -> None:
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    m = build_manifest(series, holdout_fraction=0.25, source="btc-sample")
    path = tmp_path / "holdout.json"
    m.to_json(path)
    loaded = HoldoutManifest.from_json(path)
    assert loaded == m


def test_invalid_fraction_rejected(series: Array) -> None:
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="holdout_fraction"):
            build_manifest(series, holdout_fraction=bad, source="x")
