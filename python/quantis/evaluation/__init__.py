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
from quantis.evaluation.metrics import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    max_drawdown,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    sortino_ratio,
)
from quantis.evaluation.multiple_testing import SpaResult, spa_test
from quantis.evaluation.trial_log import TrialLog, TrialRecord
from quantis.evaluation.walk_forward import (
    WalkForwardResult,
    WindowResult,
    walk_forward_evaluate,
)

__all__ = [
    "LeakageError",
    "SpaResult",
    "Split",
    "TrialLog",
    "TrialRecord",
    "WalkForwardResult",
    "WindowResult",
    "assert_no_leakage",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "max_drawdown",
    "probabilistic_sharpe_ratio",
    "purged_kfold",
    "sharpe_ratio",
    "sortino_ratio",
    "spa_test",
    "splits_from_config",
    "walk_forward",
    "walk_forward_evaluate",
]
