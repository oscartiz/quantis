"""Evaluation: walk-forward and purged k-fold CV with embargo and a leakage
guard (Phase 2); Deflated Sharpe Ratio and SPA/Reality-Check multiple-testing
corrections over the logged trial history (Phase 3)."""

from quantis.evaluation.cross_validation import (
    LeakageError,
    Split,
    assert_no_leakage,
    purged_kfold,
    splits_from_config,
    walk_forward,
)

__all__ = [
    "LeakageError",
    "Split",
    "assert_no_leakage",
    "purged_kfold",
    "splits_from_config",
    "walk_forward",
]
