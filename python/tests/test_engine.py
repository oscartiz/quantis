"""Cross-language tests: the Rust engine, driven from Python, must agree with
the CLI and with the committed golden hash. Skipped (not failed) when the
`quantis_core` extension has not been built, so a pure-Python checkout still
runs the rest of the suite; CI builds it first (see the python job)."""

import os
from pathlib import Path

import pytest

pytest.importorskip(
    "quantis_core",
    reason="build the Rust extension first: `make bindings` (maturin develop)",
)

from quantis.engine import read_mid_series, run_backtest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = "config/engine.example.toml"
_SAMPLE = "data/sample/btc-sample.qnts"


@pytest.fixture
def at_repo_root() -> object:
    """Run with CWD at the repo root so the config's relative data paths
    resolve, restoring the previous directory afterward."""
    prev = Path.cwd()
    os.chdir(_REPO_ROOT)
    try:
        yield
    finally:
        os.chdir(prev)


def test_python_backtest_reproduces_golden_hash(at_repo_root: object) -> None:
    result = run_backtest(_CONFIG)
    expected = (_REPO_ROOT / "tests" / "smoke" / "expected_hash.txt").read_text().strip()
    assert result["determinism_hash"] == expected, (
        "Python-driven backtest disagrees with the committed golden hash; "
        "the binding and the CLI must call the same Rust runner"
    )


def test_artifact_shape_is_stable(at_repo_root: object) -> None:
    result = run_backtest(_CONFIG)
    assert result["deterministic"]["seed"] == 42
    assert result["deterministic"]["instrument"] == "BTC"
    metrics = result["deterministic"]["metrics"]
    assert metrics["events"] > 0
    assert metrics["fills"] > 0
    # runtime section carries provenance but never affects the hash
    assert "git_sha" in result["runtime"]


def test_mid_series_reads_and_aligns(at_repo_root: object) -> None:
    series = read_mid_series(_SAMPLE)
    assert len(series) > 0
    assert len(series.exch_ms) == len(series.mid) == len(series.recv_ms)
    # sample BTC mid prices are in a plausible five/six-figure range
    assert all(10_000.0 < m < 1_000_000.0 for m in series.mid)
    # exchange timestamps are non-decreasing
    assert all(b >= a for a, b in zip(series.exch_ms, series.exch_ms[1:], strict=False))
