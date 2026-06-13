"""Python access to the Rust backtest engine via the `quantis_core` extension.

This module is intentionally thin. The heavy lifting — replaying events,
matching fills, accounting — happens in Rust; Python only launches a run and
parses the artifact. Because :func:`run_backtest` calls the same
``quantis_backtest::runner`` the CLI uses, a backtest launched from Python
reproduces the CLI's determinism hash byte for byte (asserted in the tests).
"""

import json
from pathlib import Path
from typing import Any

import quantis_core


def run_backtest(config_path: str | Path) -> dict[str, Any]:
    """Run a backtest from a TOML engine config; return the results artifact.

    The returned dict has the same shape as the JSON written to ``results/``:
    ``determinism_hash`` plus ``deterministic`` (seed, file hashes, metrics)
    and ``runtime`` (git SHA, timing) sections.
    """
    raw = quantis_core.run_backtest_json(str(config_path))
    result: dict[str, Any] = json.loads(raw)
    return result


def read_mid_series(log_path: str | Path) -> "MidSeries":
    """Read the mid-price series from a recorded event log.

    Returns aligned arrays over L2 snapshots with both sides present. Floats
    appear only at this research boundary; the engine uses integer fixed point.
    """
    exch_ms, recv_ms, mid = quantis_core.read_mid_series(str(log_path))
    return MidSeries(exch_ms=exch_ms, recv_ms=recv_ms, mid=mid)


class MidSeries:
    """Aligned mid-price series from an event log (one point per L2 snapshot)."""

    __slots__ = ("exch_ms", "mid", "recv_ms")

    def __init__(self, exch_ms: list[int], recv_ms: list[int], mid: list[float]) -> None:
        self.exch_ms = exch_ms
        self.recv_ms = recv_ms
        self.mid = mid

    def __len__(self) -> int:
        return len(self.mid)
