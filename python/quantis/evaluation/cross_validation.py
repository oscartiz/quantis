"""Leakage-resistant time-series cross-validation.

Two schemes, both driven by ``quantis.config.CrossValidationSpec``:

- **Walk-forward** (expanding window): train strictly on the past, test on the
  next block, with an embargo gap between them. The natural validation for a
  live strategy — every split is causal.
- **Purged k-fold** (López de Prado): contiguous test folds, with two defenses
  against leakage that ordinary k-fold ignores on serially-correlated data with
  multi-bar labels:
    1. *Purging* — drop training samples whose label window (a label may look
       ``label_span`` bars ahead) overlaps the test fold.
    2. *Embargo* — drop a buffer of training samples immediately after the test
       fold, where short-horizon serial correlation would otherwise leak test
       information into training.

The honesty mechanism is :func:`assert_no_leakage`: it checks every split for
train/test information overlap and raises if any is found — the CV-level analog
of the feature pipeline's leakage canary. ``tests/test_cross_validation.py``
proves it catches an intentionally leaky (plain, unpurged) k-fold.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from quantis.config import CrossValidationSpec

IntArray = NDArray[np.int64]


@dataclass(frozen=True)
class Split:
    """Train/test index sets for one CV fold."""

    train: IntArray
    test: IntArray


class LeakageError(AssertionError):
    """Raised when a CV split lets training information overlap the test set."""


def walk_forward(n_samples: int, n_splits: int, embargo: int = 0) -> list[Split]:
    """Expanding-window walk-forward splits.

    The timeline is cut into ``n_splits + 1`` equal blocks; block 0 is the
    initial training seed, and each later block is tested in turn while training
    on everything before it up to an ``embargo`` gap.
    """
    _check(n_samples, n_splits, embargo)
    fold = n_samples // (n_splits + 1)
    if fold == 0:
        raise ValueError("too many splits for the number of samples")

    splits: list[Split] = []
    for i in range(1, n_splits + 1):
        test_start = i * fold
        test_end = n_samples if i == n_splits else (i + 1) * fold
        train_end = max(0, test_start - embargo)
        train = np.arange(0, train_end, dtype=np.int64)
        test = np.arange(test_start, test_end, dtype=np.int64)
        if train.size and test.size:
            splits.append(Split(train, test))
    return splits


def purged_kfold(
    n_samples: int, n_splits: int, embargo: int = 0, label_span: int = 0
) -> list[Split]:
    """Purged, embargoed k-fold splits.

    ``label_span`` is how many bars ahead a label looks (0 for a point label).
    Training samples whose label window overlaps the test fold are *purged*, and
    a further ``embargo`` of samples after the test fold is dropped.
    """
    _check(n_samples, n_splits, embargo)
    if label_span < 0:
        raise ValueError("label_span must be >= 0")
    fold = n_samples // n_splits
    if fold == 0:
        raise ValueError("too many splits for the number of samples")

    all_idx = np.arange(n_samples, dtype=np.int64)
    splits: list[Split] = []
    for i in range(n_splits):
        test_start = i * fold
        test_end = n_samples if i == n_splits - 1 else (i + 1) * fold
        test = np.arange(test_start, test_end, dtype=np.int64)

        # Purge: a train sample j carries information over [j, j + label_span];
        # exclude it if that window reaches into the test fold. Embargo: also
        # exclude [test_end, test_end + embargo).
        purge_lo = test_start - label_span
        embargo_hi = test_end + embargo
        keep = (all_idx < purge_lo) | (all_idx >= embargo_hi)
        # never train on the test indices themselves
        keep &= (all_idx < test_start) | (all_idx >= test_end)
        train = all_idx[keep]
        if train.size and test.size:
            splits.append(Split(train, test))
    return splits


def splits_from_config(n_samples: int, cv: CrossValidationSpec, label_span: int = 0) -> list[Split]:
    """Build splits according to a validated research-config CV spec."""
    if cv.scheme == "walk_forward":
        return walk_forward(n_samples, cv.n_splits, cv.embargo_bars)
    return purged_kfold(n_samples, cv.n_splits, cv.embargo_bars, label_span)


def assert_no_leakage(splits: list[Split], label_span: int = 0, *, causal: bool = False) -> None:
    """Raise :class:`LeakageError` if any split leaks information.

    Checks, for every split:
      * no training sample's label window ``[j, j + label_span]`` intersects the
        ``[min(test), max(test)]`` span, and
      * if ``causal`` (walk-forward), every training index precedes every test
        index.
    """
    for s in splits:
        if s.test.size == 0 or s.train.size == 0:
            continue
        test_lo = int(s.test.min())
        test_hi = int(s.test.max())
        # a train sample j leaks if j <= test_hi and j + label_span >= test_lo
        leaks = (s.train <= test_hi) & (s.train + label_span >= test_lo)
        if np.any(leaks):
            offending = s.train[leaks][:5]
            raise LeakageError(
                f"training samples {offending.tolist()} overlap test span "
                f"[{test_lo}, {test_hi}] (label_span={label_span})"
            )
        if causal and int(s.train.max()) >= test_lo:
            raise LeakageError(
                f"non-causal split: a training index {int(s.train.max())} is not "
                f"before the test start {test_lo}"
            )


def _check(n_samples: int, n_splits: int, embargo: int) -> None:
    if n_samples < 2:
        raise ValueError("need at least 2 samples")
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
