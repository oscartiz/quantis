"""Combinatorially Symmetric Cross-Validation (CSCV) and the Probability of
Backtest Overfitting (PBO).

References: Bailey, Borwein, López de Prado & Zhu, "The Probability of Backtest
Overfitting" (2014).

The repo already deflates a search (Deflated Sharpe) and bootstrap-tests the
winner (SPA). CSCV answers a different, blunter question that those two do not:
**when I pick the in-sample-best configuration, how often is it merely
mediocre — or worse — out of sample?** It needs no distributional assumption and
no benchmark; it is a direct combinatorial measurement of selection fragility.

Method. Given a ``(T, N)`` matrix of per-period performance for ``N`` searched
configurations over ``T`` periods:

1. cut the ``T`` rows into ``S`` disjoint contiguous blocks (``S`` even);
2. for every way of choosing ``S/2`` blocks as the in-sample (IS) set — there are
   ``C(S, S/2)`` symmetric splits — the complement is the out-of-sample (OOS) set;
3. on each split, rank the configs by IS performance, take the IS winner, and
   find its **OOS rank** as a relative position ``ω ∈ (0, 1)``; its logit is
   ``λ = ln(ω / (1-ω))``;
4. **PBO = fraction of splits where the IS winner lands at or below the OOS
   median** (``λ ≤ 0``) — the probability that in-sample selection buys you
   nothing out of sample.

A PBO near 0.5 means the search is pure overfitting (the IS winner is an OOS
coin flip); near 0 means selection is robust. Performance degradation (IS vs OOS
of the selected config) and the probability the selected config loses OOS fall
out of the same splits.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
from numpy.typing import NDArray
from scipy.stats import rankdata

Array = NDArray[np.float64]


@dataclass(frozen=True)
class PboResult:
    """Outcome of a CSCV pass over a ``(T, N)`` performance matrix."""

    pbo: float  # P(IS-best config is OOS at or below median) — the headline
    logits: Array  # (n_splits,): logit of the IS winner's OOS relative rank
    n_splits: int  # C(S, S/2) symmetric splits evaluated
    n_blocks: int  # S
    n_configs: int  # N
    prob_oos_loss: float  # fraction of splits where the selected config's OOS Sharpe < 0
    median_oos_sharpe_selected: float  # median OOS Sharpe of the IS-selected config
    median_is_sharpe_selected: float  # median IS Sharpe of the IS-selected config


def _sharpe_columns(block: Array) -> Array:
    """Per-column (per-config) Sharpe over the rows of ``block``; 0 where flat."""
    mean: Array = block.mean(axis=0)
    std: Array = block.std(axis=0, ddof=1)
    out: Array = np.zeros_like(mean)
    np.divide(mean, std, out=out, where=std > 0.0)  # safe: no 0/0 in the flat columns
    return out


def cscv_pbo(performance: Array, *, n_blocks: int = 10) -> PboResult:
    """Probability of Backtest Overfitting via CSCV.

    ``performance`` is a ``(T, N)`` matrix of per-period performance (e.g. returns)
    for ``N`` configs over ``T`` periods. ``n_blocks`` (``S``, even) is the number
    of contiguous blocks the timeline is cut into; the number of symmetric splits
    is ``C(S, S/2)``. Rows beyond an exact multiple of ``S`` are dropped.
    """
    d = np.asarray(performance, dtype=np.float64)
    if d.ndim != 2:
        raise ValueError("performance must be a 2-D (T, N) matrix")
    t_len, n = d.shape
    if n < 2:
        raise ValueError("need at least 2 configurations to rank")
    if n_blocks < 4 or n_blocks % 2 != 0:
        raise ValueError("n_blocks must be even and >= 4")
    block_len = t_len // n_blocks
    if block_len < 2:
        raise ValueError("too few observations for this many blocks")
    if not np.all(np.isfinite(d)):
        raise ValueError("performance contains NaN or inf")

    # Trim to an exact multiple of S and split into S contiguous blocks.
    usable = block_len * n_blocks
    blocks = np.array_split(d[:usable], n_blocks, axis=0)

    half = n_blocks // 2
    logits = []
    oos_sharpes_sel = []
    is_sharpes_sel = []
    n_loss = 0
    for is_idx in combinations(range(n_blocks), half):
        is_set = set(is_idx)
        is_rows = np.vstack([blocks[b] for b in range(n_blocks) if b in is_set])
        oos_rows = np.vstack([blocks[b] for b in range(n_blocks) if b not in is_set])

        is_perf = _sharpe_columns(is_rows)
        oos_perf = _sharpe_columns(oos_rows)
        best = int(np.argmax(is_perf))

        # OOS relative rank of the IS winner, ties averaged: omega in (0, 1).
        oos_rank = float(rankdata(oos_perf)[best])  # 1..N, ascending (N = best OOS)
        omega = oos_rank / (n + 1.0)
        omega = min(max(omega, 1e-6), 1.0 - 1e-6)
        logits.append(np.log(omega / (1.0 - omega)))

        oos_sharpes_sel.append(oos_perf[best])
        is_sharpes_sel.append(is_perf[best])
        if oos_perf[best] < 0.0:
            n_loss += 1

    logit_arr = np.array(logits, dtype=np.float64)
    n_splits = logit_arr.size
    return PboResult(
        pbo=float(np.mean(logit_arr <= 0.0)),
        logits=logit_arr,
        n_splits=n_splits,
        n_blocks=n_blocks,
        n_configs=n,
        prob_oos_loss=float(n_loss / n_splits),
        median_oos_sharpe_selected=float(np.median(oos_sharpes_sel)),
        median_is_sharpe_selected=float(np.median(is_sharpes_sel)),
    )
