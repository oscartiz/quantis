"""A persistent log of every research trial.

Honest multiple-testing correction is only possible if you actually recorded
how many things you tried. This log is that record: each trial appends one JSON
line with its identity and its per-period returns. The Deflated Sharpe Ratio
reads the trial Sharpes; SPA reads the returns matrix. Computing those over a
log written *during* research — rather than a trial count guessed afterward —
is what makes the correction trustworthy rather than decorative.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from quantis.evaluation.metrics import sharpe_ratio

Array = NDArray[np.float64]


@dataclass(frozen=True)
class TrialRecord:
    """One research trial: its identity and realized per-period returns."""

    name: str
    returns: list[float] = field(default_factory=list)
    config_hash: str = ""

    def sharpe(self) -> float:
        """Per-period Sharpe of this trial's returns."""
        return sharpe_ratio(np.asarray(self.returns, dtype=np.float64))

    def to_json_line(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json_line(line: str) -> TrialRecord:
        data = json.loads(line)
        return TrialRecord(
            name=data["name"],
            returns=list(data["returns"]),
            config_hash=data.get("config_hash", ""),
        )


class TrialLog:
    """Append-only JSONL log of trials, backed by a file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: TrialRecord) -> None:
        """Append one trial; creates the file if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_json_line() + "\n")

    def load(self) -> list[TrialRecord]:
        """All trials in append order (empty list if the log does not exist)."""
        if not self.path.exists():
            return []
        return [
            TrialRecord.from_json_line(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def sharpes(self) -> Array:
        """Per-period Sharpe of every logged trial (for Sharpe deflation)."""
        return np.array([t.sharpe() for t in self.load()], dtype=np.float64)

    def performance_matrix(self) -> Array:
        """``(T, K)`` matrix of trial returns for SPA. Requires all trials to
        share the same number of observations (raises otherwise)."""
        trials = self.load()
        if not trials:
            raise ValueError("no trials logged")
        lengths = {len(t.returns) for t in trials}
        if len(lengths) != 1:
            raise ValueError(f"trials have differing lengths {sorted(lengths)}; cannot align")
        return np.column_stack([np.asarray(t.returns, dtype=np.float64) for t in trials])
