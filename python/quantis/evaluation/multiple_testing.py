"""Hansen's SPA test and White's Reality Check over a set of strategies.

When you search over many strategies and report the best, the best one's
performance is biased upward by selection. These tests ask the honest question:
*is the best strategy's outperformance real, or is it what you'd expect from
searching this hard?* They give a p-value for the null "no strategy beats the
benchmark", correcting for the whole set of trials — not just the winner.

- **White's Reality Check (RC)** recenters every strategy, so genuinely bad
  strategies inflate the null distribution and make the test conservative.
- **Hansen's SPA** excludes strategies that are too far below the benchmark from
  the recentering, recovering power while staying valid. SPA is the one to lead
  with; RC is reported alongside as the conservative bound.

Dependence in time is preserved with the stationary bootstrap (Politis &
Romano, 1994). References: White (2000); Hansen (2005).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


@dataclass(frozen=True)
class SpaResult:
    """Outcome of an SPA / Reality Check run."""

    best_strategy: int
    best_mean_performance: float
    test_statistic: float
    spa_pvalue: float
    reality_check_pvalue: float
    n_strategies: int
    n_obs: int
    n_bootstrap: int


def _stationary_bootstrap_indices(
    n: int, block_size: float, rng: np.random.Generator
) -> NDArray[np.int64]:
    """Politis-Romano stationary bootstrap index path of length ``n``."""
    p = 1.0 / block_size
    idx = np.empty(n, dtype=np.int64)
    idx[0] = rng.integers(n)
    for t in range(1, n):
        if rng.random() < p:
            idx[t] = rng.integers(n)
        else:
            idx[t] = (idx[t - 1] + 1) % n
    return idx


def spa_test(
    performance: Array,
    *,
    block_size: float = 10.0,
    n_bootstrap: int = 2000,
    seed: int = 0,
) -> SpaResult:
    """Test whether the best column of ``performance`` beats a zero benchmark.

    ``performance`` is a ``(T, K)`` matrix of per-period performance
    differentials versus the benchmark (e.g. excess returns) for ``K``
    strategies over ``T`` periods. Higher is better; the benchmark is 0, so
    feed excess-of-benchmark series.
    """
    d = np.asarray(performance, dtype=np.float64)
    if d.ndim != 2:
        raise ValueError("performance must be a 2-D (T, K) matrix")
    t_len, k = d.shape
    if t_len < 8 or k < 1:
        raise ValueError("need at least 8 observations and 1 strategy")
    if not np.all(np.isfinite(d)):
        raise ValueError("performance contains NaN or inf")

    rng = np.random.default_rng(seed)
    sqrt_t = np.sqrt(t_len)
    dbar = d.mean(axis=0)  # (K,)

    # Bootstrap means, used both to studentize and to form the null.
    boot_means = np.empty((n_bootstrap, k))
    for b in range(n_bootstrap):
        idx = _stationary_bootstrap_indices(t_len, block_size, rng)
        boot_means[b] = d[idx].mean(axis=0)

    centered = sqrt_t * (boot_means - dbar)  # (B, K)
    omega = np.sqrt(np.mean(centered**2, axis=0))  # (K,)
    omega = np.where(omega > 0.0, omega, np.inf)  # zero-variance => never selected

    z = sqrt_t * dbar / omega  # (K,) observed studentized
    t_obs = max(float(np.max(z)), 0.0)

    w = centered / omega  # (B, K) studentized recentered bootstrap

    # Reality Check: every strategy contributes to the null.
    rc_stat = np.maximum(np.max(w, axis=1), 0.0)
    rc_p = float(np.mean(rc_stat >= t_obs))

    # SPA: drop strategies too far below the benchmark from the null.
    threshold = -np.sqrt(2.0 * np.log(np.log(t_len)))
    relevant = z >= threshold
    if np.any(relevant):
        spa_stat = np.maximum(np.max(w[:, relevant], axis=1), 0.0)
    else:
        spa_stat = np.zeros(n_bootstrap)
    spa_p = float(np.mean(spa_stat >= t_obs))

    best = int(np.argmax(dbar))
    return SpaResult(
        best_strategy=best,
        best_mean_performance=float(dbar[best]),
        test_statistic=t_obs,
        spa_pvalue=spa_p,
        reality_check_pvalue=rc_p,
        n_strategies=k,
        n_obs=t_len,
        n_bootstrap=n_bootstrap,
    )
