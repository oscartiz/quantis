"""Performance metrics, with multiple-testing-aware Sharpe deflation.

The headline number a backtest produces — the Sharpe ratio — is the easiest
statistic in finance to fool yourself with: try enough strategies and the best
one's Sharpe looks impressive by luck alone. This module provides the honest
counterparts:

- :func:`probabilistic_sharpe_ratio` — the probability the true Sharpe exceeds a
  benchmark, accounting for sample length and the non-normality (skew, fat
  tails) of returns.
- :func:`deflated_sharpe_ratio` — the PSR with the benchmark set to the
  *expected maximum* Sharpe across the trials you actually ran. It answers:
  "given that I searched over N strategies, is this one's Sharpe still
  significant?" A high in-sample Sharpe can have a DSR near 0.5 (i.e. no real
  edge) once deflated — that is the number to report, not the raw Sharpe.

References: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.stats import kurtosis, norm, skew

Array = NDArray[np.float64]

_EULER_MASCHERONI = 0.5772156649015329


def sharpe_ratio(returns: Array, periods_per_year: float = 1.0) -> float:
    """Annualized Sharpe ratio of a per-period returns series (excess of 0).

    ``periods_per_year`` annualizes; pass 1.0 for the raw per-period Sharpe
    (which the deflation functions expect).
    """
    r = _clean(returns)
    sd = r.std(ddof=1)
    if sd == 0.0:
        return 0.0
    return float(r.mean() / sd * np.sqrt(periods_per_year))


def sortino_ratio(returns: Array, periods_per_year: float = 1.0, target: float = 0.0) -> float:
    """Annualized Sortino ratio: excess mean over downside deviation."""
    r = _clean(returns)
    downside = np.minimum(r - target, 0.0)
    dd = np.sqrt(np.mean(downside**2))
    if dd == 0.0:
        return 0.0
    return float((r.mean() - target) / dd * np.sqrt(periods_per_year))


def max_drawdown(returns: Array) -> float:
    """Maximum peak-to-trough fractional drawdown of the compounded equity
    curve implied by ``returns`` (simple per-period returns)."""
    r = _clean(returns)
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    return float(-drawdown.min()) if drawdown.size else 0.0


def probabilistic_sharpe_ratio(
    returns: Array, benchmark_sr: float = 0.0, periods_per_year: float = 1.0
) -> float:
    """P(true Sharpe > ``benchmark_sr``) given the observed sample.

    Uses the per-period Sharpe and the sample's skew/kurtosis; longer samples
    and more normal returns give a more confident estimate. Returns a
    probability in [0, 1].
    """
    r = _clean(returns)
    n = r.size
    if n < 3:
        return float("nan")
    sr = sharpe_ratio(r, periods_per_year=1.0)
    bench = benchmark_sr / np.sqrt(periods_per_year)
    g3 = float(skew(r, bias=False))
    g4 = float(kurtosis(r, fisher=False, bias=False))  # non-excess (normal = 3)
    denom = np.sqrt(1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr**2)
    if denom <= 0.0 or not np.isfinite(denom):
        return float("nan")
    z = (sr - bench) * np.sqrt(n - 1) / denom
    return float(norm.cdf(z))


def expected_max_sharpe(trial_sharpes: Array) -> float:
    """Expected maximum of ``N`` independent Sharpe estimates under the null of
    zero true Sharpe — the deflation benchmark.

    ``E[max] ~ sqrt(V) * [ (1-g)*Zinv(1 - 1/N) + g*Zinv(1 - 1/(N*e)) ]`` where
    ``V`` is the cross-trial variance of the Sharpe ratios, ``g`` is the
    Euler-Mascheroni constant, and ``Zinv`` is the inverse normal CDF. This
    grows with the number of trials: more searching means a higher bar to clear.
    """
    sr = _clean(trial_sharpes)
    n = sr.size
    if n < 2:
        return 0.0
    v = sr.var(ddof=1)
    if v <= 0.0:
        return 0.0
    g = _EULER_MASCHERONI
    z1 = float(norm.ppf(1.0 - 1.0 / n))
    z2 = float(norm.ppf(1.0 - 1.0 / (n * np.e)))
    return float(np.sqrt(v) * ((1.0 - g) * z1 + g * z2))


def deflated_sharpe_ratio(
    returns: Array, trial_sharpes: Array, periods_per_year: float = 1.0
) -> float:
    """Deflated Sharpe Ratio: PSR with the benchmark set to the expected maximum
    Sharpe across the ``trial_sharpes`` actually searched.

    ``returns`` are the per-period returns of the selected strategy;
    ``trial_sharpes`` are the per-period Sharpe ratios of every trial run
    (from the trial log). A DSR below ~0.95 means the strategy's Sharpe is not
    convincingly better than the best you would expect from luck given how many
    strategies you tried.
    """
    benchmark = expected_max_sharpe(trial_sharpes) * np.sqrt(periods_per_year)
    return probabilistic_sharpe_ratio(returns, benchmark, periods_per_year)


def _clean(returns: Array) -> Array:
    r = np.asarray(returns, dtype=np.float64).ravel()
    if not np.all(np.isfinite(r)):
        raise ValueError("returns contain NaN or inf")
    if r.size == 0:
        raise ValueError("returns are empty")
    return r
