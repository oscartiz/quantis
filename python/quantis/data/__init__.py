"""Data access: loaders for recorded event logs and candle history, plus the
holdout wall — the walled-off out-of-sample span whose hash is committed before
research begins and which is evaluated exactly once."""

from quantis.data.holdout import (
    ACKNOWLEDGEMENT,
    HoldoutManifest,
    HoldoutSealed,
    build_manifest,
    load_research,
    reveal_holdout,
)

__all__ = [
    "ACKNOWLEDGEMENT",
    "HoldoutManifest",
    "HoldoutSealed",
    "build_manifest",
    "load_research",
    "reveal_holdout",
]
