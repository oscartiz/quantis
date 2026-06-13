"""Bayesian Online Changepoint Detection (Adams & MacKay, 2007).

**Hypothesis this model encodes.** The parameters generating returns (here the
mean and variance) are *piecewise constant*: the series is divided into
segments, and at each step there is a constant probability — the *hazard* — that
the current segment ends and a fresh one begins with new parameters. The model
infers, online, the posterior distribution over the *run length* (time since the
last changepoint) using only data seen so far.

**Why pair it with the HMM.** The Gaussian HMM ([`quantis.models.hmm`]) assumes
a fixed, small set of *recurring* regimes and estimates them with full-sample
smoothing — a powerful but *non-causal*, offline lens. BOCPD assumes
*non-recurring* segments and is *causal by construction* — its output at time
``t`` depends only on ``x[:t+1]``. Comparing what each detects on the same data
is a concrete lesson in look-ahead: the HMM can "know" a regime label using the
future; BOCPD cannot. ADR-003 records this trade-off as the reason both ship.

Conjugacy: a Normal-Inverse-Gamma prior on (mean, variance) yields a Student-t
posterior predictive, updated in closed form. All recursions are in log space.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.special import gammaln, logsumexp

Array = NDArray[np.float64]


@dataclass(frozen=True)
class NormalInverseGammaPrior:
    """Conjugate prior on a Gaussian's unknown mean and variance.

    ``mu`` is the prior mean; ``kappa`` its strength (pseudo-observations);
    ``alpha``, ``beta`` shape/scale of the inverse-gamma on the variance.
    """

    mu: float = 0.0
    kappa: float = 1.0
    alpha: float = 1.0
    beta: float = 1.0

    def validate(self) -> None:
        if self.kappa <= 0 or self.alpha <= 0 or self.beta <= 0:
            raise ValueError("kappa, alpha, beta must be positive")


@dataclass(frozen=True)
class BocpdResult:
    """Output of an online BOCPD pass."""

    run_length_posterior: Array  # (T, R): P(run length = r | x[:t+1])
    changepoint_prob: Array  # (T,): posterior mass on run length 0 at each t
    map_run_length: NDArray[np.int64]  # (T,): argmax run length at each t

    def changepoint_indices(self, min_drop: int = 10) -> NDArray[np.int64]:
        """Inferred changepoint *locations*, detected online via run-length
        resets.

        Under normal growth the MAP run length increments by one each step; a
        sharp drop (> ``min_drop``) means the model just became confident a new
        segment began. The inferred changepoint time is the detection time
        minus the (now small) run length — i.e. when the new segment started.

        This, not the bare run-length-0 mass, is the right changepoint signal:
        the model typically only *recognizes* a changepoint a step or two after
        it occurs, by which point the posterior mass has moved off run length 0.
        """
        diffs = np.diff(self.map_run_length)
        detections = np.flatnonzero(diffs < -min_drop) + 1
        inferred = detections - self.map_run_length[detections]
        out: NDArray[np.int64] = np.unique(np.clip(inferred, 0, None)).astype(np.int64)
        return out


class Bocpd:
    """Online changepoint detector with a Student-t predictive.

    Parameters
    ----------
    hazard_lambda:
        Expected segment length; the per-step changepoint probability is
        ``1 / hazard_lambda`` (a geometric prior on run length).
    prior:
        Normal-Inverse-Gamma prior used at the start of every segment.
    max_run_length:
        Cap on tracked run length (truncation keeps the pass O(T·R); set well
        above the longest plausible segment).
    """

    def __init__(
        self,
        hazard_lambda: float = 250.0,
        *,
        prior: NormalInverseGammaPrior | None = None,
        max_run_length: int = 500,
    ) -> None:
        if hazard_lambda <= 1.0:
            raise ValueError("hazard_lambda must be > 1")
        if max_run_length < 2:
            raise ValueError("max_run_length must be >= 2")
        self.hazard = 1.0 / hazard_lambda
        self.prior = prior or NormalInverseGammaPrior()
        self.prior.validate()
        self.max_run_length = max_run_length

    def fit_predict(self, x: Array) -> BocpdResult:
        """Run the forward recursion over a 1-D series."""
        obs = np.asarray(x, dtype=np.float64)
        if obs.ndim != 1:
            raise ValueError("BOCPD operates on a 1-D series")
        if not np.all(np.isfinite(obs)):
            raise ValueError("series contains NaN or inf (drop warmup rows first)")

        n = obs.shape[0]
        r_max = self.max_run_length
        log_h = np.log(self.hazard)
        log_1mh = np.log1p(-self.hazard)

        # Per-run-length sufficient statistics, seeded with the prior at r=0.
        mu = np.array([self.prior.mu])
        kappa = np.array([self.prior.kappa])
        alpha = np.array([self.prior.alpha])
        beta = np.array([self.prior.beta])

        # Joint log P(run length r, x[:t]); starts certain at run length 0.
        log_joint = np.array([0.0])

        rl_post = np.zeros((n, r_max))
        cp_prob = np.zeros(n)
        map_rl = np.zeros(n, dtype=np.int64)

        for t in range(n):
            log_pred = _student_t_logpdf(obs[t], mu, kappa, alpha, beta)

            # Growth: stay in the segment (run length r -> r+1).
            log_growth = log_joint + log_pred + log_1mh
            # Changepoint: reset to run length 0, summing over all current r.
            log_cp = float(logsumexp(log_joint + log_pred + log_h))

            new_joint = np.empty(log_growth.shape[0] + 1)
            new_joint[0] = log_cp
            new_joint[1:] = log_growth

            # Update sufficient stats: r=0 gets the prior; r+1 absorbs x[t].
            up_mu, up_kappa, up_alpha, up_beta = _nig_update(obs[t], mu, kappa, alpha, beta)
            mu = np.concatenate(([self.prior.mu], up_mu))
            kappa = np.concatenate(([self.prior.kappa], up_kappa))
            alpha = np.concatenate(([self.prior.alpha], up_alpha))
            beta = np.concatenate(([self.prior.beta], up_beta))

            # Truncate to the run-length cap (drop the oldest/longest tail).
            if new_joint.shape[0] > r_max:
                new_joint = new_joint[:r_max]
                mu, kappa = mu[:r_max], kappa[:r_max]
                alpha, beta = alpha[:r_max], beta[:r_max]

            # Normalize to the run-length posterior at time t.
            log_norm = float(logsumexp(new_joint))
            log_joint = new_joint - log_norm
            post = np.exp(log_joint)

            rl_post[t, : post.shape[0]] = post
            cp_prob[t] = post[0]
            map_rl[t] = int(np.argmax(post))

        return BocpdResult(rl_post, cp_prob, map_rl)


def _student_t_logpdf(x: float, mu: Array, kappa: Array, alpha: Array, beta: Array) -> Array:
    """Log posterior-predictive density per run length: Student-t with
    ``df = 2 alpha``, ``loc = mu``, ``scale^2 = beta (kappa + 1) / (alpha kappa)``."""
    df = 2.0 * alpha
    scale_sq = beta * (kappa + 1.0) / (alpha * kappa)
    z = (x - mu) ** 2 / (df * scale_sq)
    logpdf: Array = (
        gammaln((df + 1.0) / 2.0)
        - gammaln(df / 2.0)
        - 0.5 * np.log(df * np.pi * scale_sq)
        - (df + 1.0) / 2.0 * np.log1p(z)
    )
    return logpdf


def _nig_update(
    x: float, mu: Array, kappa: Array, alpha: Array, beta: Array
) -> tuple[Array, Array, Array, Array]:
    """Closed-form Normal-Inverse-Gamma update absorbing one observation."""
    kappa_new = kappa + 1.0
    mu_new = (kappa * mu + x) / kappa_new
    alpha_new = alpha + 0.5
    beta_new = beta + (kappa * (x - mu) ** 2) / (2.0 * kappa_new)
    return mu_new, kappa_new, alpha_new, beta_new
