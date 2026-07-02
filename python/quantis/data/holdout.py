"""The holdout wall: a walled-off out-of-sample partition, evaluated once.

The single most important discipline in honest backtesting is an out-of-sample
set that research never touches. This module enforces it mechanically rather
than relying on good intentions:

1. **Seal.** Before any research, :func:`build_manifest` splits the data
   *chronologically* — the holdout is the most recent ``holdout_fraction`` of
   the series (the realistic future), never a random sample — and records the
   boundary plus a SHA-256 of the holdout values. The manifest is committed to
   the repo, so the boundary and the holdout's content hash are fixed and
   auditable from that commit forward.
2. **Research.** :func:`load_research` returns *only* the research partition and
   refuses to hand back anything at or beyond the sealed boundary.
3. **Reveal once.** :func:`reveal_holdout` is the *only* way to obtain the
   holdout. It requires an explicit acknowledgement string and re-verifies the
   holdout hash against the committed manifest — proving the data was not
   altered and that exactly the sealed span is being evaluated. By convention
   (and per the README), this is called a single time, at the very end.

The wall is only as strong as the discipline of committing the manifest before
research and not calling :func:`reveal_holdout` until the end — but making the
boundary and hash a committed artifact means any breach is visible in history.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]

ACKNOWLEDGEMENT = "I am evaluating the holdout exactly once and will not tune on it"


class HoldoutSealed(Exception):
    """Raised on an attempt to access the holdout without the proper ritual."""


@dataclass(frozen=True)
class HoldoutManifest:
    """Committed description of a sealed holdout partition."""

    source: str
    n_total: int
    n_research: int
    n_holdout: int
    boundary_index: int
    holdout_fraction: float
    holdout_sha256: str

    def to_json(self, path: str | Path) -> None:
        """Write the manifest as pretty-printed JSON."""
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def from_json(path: str | Path) -> HoldoutManifest:
        """Load a manifest previously written by :meth:`to_json`."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return HoldoutManifest(**data)


def _hash_values(values: Array) -> str:
    """SHA-256 of the canonical little-endian float64 bytes of ``values``."""
    contiguous = np.ascontiguousarray(values, dtype="<f8")
    return hashlib.sha256(contiguous.tobytes()).hexdigest()


def build_manifest(values: Array, holdout_fraction: float, source: str) -> HoldoutManifest:
    """Seal the most recent ``holdout_fraction`` of ``values`` as the holdout.

    The split is by position (the series is assumed time-ordered), so the
    holdout is genuinely the future relative to the research partition.
    """
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be in (0, 1)")
    series = np.asarray(values, dtype=np.float64).ravel()
    n = series.shape[0]
    boundary = int(np.floor(n * (1.0 - holdout_fraction)))
    if boundary < 1 or boundary >= n:
        raise ValueError("holdout_fraction leaves no research or no holdout data")
    return HoldoutManifest(
        source=source,
        n_total=n,
        n_research=boundary,
        n_holdout=n - boundary,
        boundary_index=boundary,
        holdout_fraction=holdout_fraction,
        holdout_sha256=_hash_values(series[boundary:]),
    )


def load_research(values: Array, manifest: HoldoutManifest) -> Array:
    """Return only the research partition ``values[:boundary]``.

    Verifies the series length matches the sealed manifest so a silently
    changed dataset cannot shift the boundary undetected.
    """
    series = np.asarray(values, dtype=np.float64).ravel()
    if series.shape[0] != manifest.n_total:
        raise HoldoutSealed(
            f"data length {series.shape[0]} != sealed n_total {manifest.n_total}; "
            "the dataset changed after sealing — re-seal deliberately or fix the data"
        )
    return series[: manifest.boundary_index]


def reveal_holdout(values: Array, manifest: HoldoutManifest, acknowledgement: str) -> Array:
    """Return the holdout partition — the gated, evaluate-once path.

    Requires the exact :data:`ACKNOWLEDGEMENT` string and re-verifies the
    holdout hash against the committed manifest.
    """
    if acknowledgement != ACKNOWLEDGEMENT:
        raise HoldoutSealed(
            "holdout access requires the exact acknowledgement string "
            f"{ACKNOWLEDGEMENT!r}; this set is meant to be evaluated exactly once"
        )
    series = np.asarray(values, dtype=np.float64).ravel()
    if series.shape[0] != manifest.n_total:
        raise HoldoutSealed("data length does not match the sealed manifest")
    holdout = series[manifest.boundary_index :]
    actual = _hash_values(holdout)
    if actual != manifest.holdout_sha256:
        raise HoldoutSealed(
            "holdout hash mismatch: the holdout data differs from what was sealed "
            f"(sealed {manifest.holdout_sha256[:12]}…, got {actual[:12]}…)"
        )
    return holdout
