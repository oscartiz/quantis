"""Cross-validation: causality of walk-forward, the purge+embargo guarantee,
and the leakage guard (which must catch a deliberately leaky plain k-fold)."""

import numpy as np
import pytest

from quantis.config import CrossValidationSpec
from quantis.evaluation.cross_validation import (
    LeakageError,
    Split,
    assert_no_leakage,
    purged_kfold,
    splits_from_config,
    walk_forward,
)


def test_walk_forward_is_strictly_causal() -> None:
    splits = walk_forward(n_samples=1000, n_splits=4, embargo=10)
    assert len(splits) == 4
    for s in splits:
        # every training index precedes every test index, with the embargo gap
        assert s.train.max() < s.test.min()
        assert s.test.min() - s.train.max() > 10
    # expanding window: training sets grow
    sizes = [s.train.size for s in splits]
    assert sizes == sorted(sizes)
    assert_no_leakage(splits, label_span=5, causal=True)


def test_purged_kfold_purges_and_embargoes() -> None:
    n, label_span, embargo = 1000, 20, 15
    splits = purged_kfold(n, n_splits=5, embargo=embargo, label_span=label_span)
    assert len(splits) == 5
    for s in splits:
        test_lo, test_hi = int(s.test.min()), int(s.test.max())
        # no train index within the purge window before the test fold
        assert np.all((s.train < test_lo - label_span) | (s.train > test_hi))
        # no train index within the embargo window after the test fold
        assert not np.any((s.train > test_hi) & (s.train <= test_hi + embargo))
    assert_no_leakage(splits, label_span=label_span)


def test_leakage_guard_catches_unpurged_kfold() -> None:
    """The canary: a naive contiguous k-fold WITHOUT purging leaks when labels
    span multiple bars, and the guard must flag it."""
    n, n_splits, label_span = 600, 3, 30
    fold = n // n_splits
    leaky: list[Split] = []
    for i in range(n_splits):
        test = np.arange(i * fold, (i + 1) * fold, dtype=np.int64)
        train = np.array(
            [j for j in range(n) if not (i * fold <= j < (i + 1) * fold)], dtype=np.int64
        )
        leaky.append(Split(train, test))

    # the train sample just before each interior test fold has a label window
    # that reaches into the test fold -> leakage
    with pytest.raises(LeakageError, match="overlap test span"):
        assert_no_leakage(leaky, label_span=label_span)


def test_purged_kfold_has_no_leakage_where_naive_does() -> None:
    # same setup as the canary, but purged: the guard passes
    splits = purged_kfold(600, n_splits=3, embargo=0, label_span=30)
    assert_no_leakage(splits, label_span=30)


def test_walk_forward_guard_rejects_noncausal_split() -> None:
    bad = [
        Split(
            train=np.array([0, 1, 2, 50], dtype=np.int64), test=np.array([40, 41], dtype=np.int64)
        )
    ]
    with pytest.raises(LeakageError):
        assert_no_leakage(bad, label_span=0, causal=True)


def test_splits_from_config_dispatches() -> None:
    wf = CrossValidationSpec(scheme="walk_forward", n_splits=3, embargo_bars=5)
    pk = CrossValidationSpec(scheme="purged_kfold", n_splits=3, embargo_bars=5)
    assert len(splits_from_config(900, wf)) == 3
    assert len(splits_from_config(900, pk, label_span=10)) == 3
    assert_no_leakage(splits_from_config(900, wf), causal=True)
    assert_no_leakage(splits_from_config(900, pk, label_span=10), label_span=10)


def test_rejects_too_many_splits() -> None:
    with pytest.raises(ValueError, match="too many splits"):
        walk_forward(n_samples=3, n_splits=5)
    with pytest.raises(ValueError, match="n_splits"):
        purged_kfold(100, n_splits=1)
