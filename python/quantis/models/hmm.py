"""Gaussian hidden Markov model with hand-rolled Baum-Welch EM.

**Hypothesis this model encodes.** Asset returns are emitted by a small number
of unobserved *regimes* (here three: bear / chop / bull). Each regime has its
own mean drift and volatility, and the regime evolves as a first-order Markov
chain — regimes persist, and the probability of switching is constant per step.
This is a *global, offline* view: the most likely regime at time ``t`` is
estimated using the whole sample (smoothing), so it is a research/analysis lens,
not a causal trading signal. The causal counterpart is BOCPD ([`quantis.models.bocpd`]);
contrasting the two is itself a lesson in look-ahead (ADR-003).

Implementation notes:
- Diagonal covariance (standard and stable for financial regimes; matches
  ``hmmlearn``'s ``covariance_type="diag"``, against which this is validated).
- All recursions run in log space with log-sum-exp for numerical stability.
- Variances are floored to avoid the collapse-to-a-point degeneracy of
  Gaussian EM.
- Everything derives from a seed, so a fit is reproducible.

This is written from the algorithm, not wrapped from a library: the validation
test asserts it matches ``hmmlearn`` on the same data. The library stays a
dev-only test oracle and is never imported by shipped code.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.special import logsumexp

Array = NDArray[np.float64]

_LOG_2PI = float(np.log(2.0 * np.pi))


@dataclass(frozen=True)
class HmmParams:
    """Fitted parameters of a diagonal-covariance Gaussian HMM."""

    startprob: Array  # (K,)
    transmat: Array  # (K, K), row-stochastic
    means: Array  # (K, D)
    variances: Array  # (K, D), > 0

    @property
    def n_states(self) -> int:
        return int(self.means.shape[0])

    @property
    def n_features(self) -> int:
        return int(self.means.shape[1])


class GaussianHMM:
    """A diagonal-covariance Gaussian HMM trained by Baum-Welch (EM).

    Parameters
    ----------
    n_states:
        Number of latent regimes.
    n_iter:
        Maximum EM iterations.
    tol:
        Convergence threshold on the per-sample log-likelihood increase.
    var_floor:
        Lower bound on each variance, preventing singular components.
    seed:
        Seeds k-means++ initialization and all randomness.
    """

    def __init__(
        self,
        n_states: int = 3,
        *,
        n_iter: int = 100,
        tol: float = 1e-4,
        var_floor: float = 1e-8,
        seed: int = 0,
        init: HmmParams | None = None,
    ) -> None:
        if n_states < 1:
            raise ValueError("n_states must be >= 1")
        if init is not None and init.n_states != n_states:
            raise ValueError("init n_states disagrees with n_states")
        self.n_states = n_states
        self.n_iter = n_iter
        self.tol = tol
        self.var_floor = var_floor
        self.seed = seed
        # Optional explicit start, bypassing k-means++ init. Useful for warm
        # starts and for oracle comparisons that must share an EM start point.
        self.init = init
        self.params_: HmmParams | None = None
        self.log_likelihood_: float = -np.inf
        self.n_iter_run_: int = 0
        self.converged_: bool = False

    # --- public API -----------------------------------------------------------

    def fit(self, x: Array) -> GaussianHMM:
        """Fit by EM. ``x`` is ``(T, D)`` (a 1-D series is treated as ``(T, 1)``)."""
        obs = _ensure_2d(x)
        rng = np.random.default_rng(self.seed)
        if self.init is not None:
            if self.init.n_features != obs.shape[1]:
                raise ValueError("init n_features disagrees with data")
            params = self.init
        else:
            params = self._init_params(obs, rng)

        prev_ll = -np.inf
        for iteration in range(1, self.n_iter + 1):
            log_b = _log_gaussian(obs, params.means, params.variances)
            log_alpha, ll = self._forward(params, log_b)
            log_beta = self._backward(params, log_b)
            gamma, xi_sum = self._posteriors(params, log_b, log_alpha, log_beta, ll)
            params = self._m_step(obs, gamma, xi_sum)

            self.n_iter_run_ = iteration
            per_sample = ll / obs.shape[0]
            if per_sample - prev_ll < self.tol and iteration > 1:
                self.converged_ = True
                prev_ll = per_sample
                break
            prev_ll = per_sample

        self.params_ = params
        self.log_likelihood_ = self._score(params, obs)
        return self

    def score(self, x: Array) -> float:
        """Total log-likelihood of ``x`` under the fitted model."""
        return self._score(self._require_params_match(x), _ensure_2d(x))

    def predict_proba(self, x: Array) -> Array:
        """Smoothed state posteriors ``gamma[t, k]`` (uses the whole sequence)."""
        params = self._require_params_match(x)
        obs = _ensure_2d(x)
        log_b = _log_gaussian(obs, params.means, params.variances)
        log_alpha, ll = self._forward(params, log_b)
        log_beta = self._backward(params, log_b)
        gamma, _ = self._posteriors(params, log_b, log_alpha, log_beta, ll)
        return gamma

    def predict(self, x: Array) -> NDArray[np.int64]:
        """Most likely state sequence (Viterbi MAP decoding)."""
        params = self._require_params_match(x)
        return self._viterbi(params, _ensure_2d(x))

    def regime_order(self) -> NDArray[np.int64]:
        """State indices sorted by mean of feature 0 (ascending).

        Resolves label-switching: for return-like feature 0, this orders states
        bear → chop → bull, so callers can attach economic meaning to otherwise
        arbitrary state indices.
        """
        params = self._require_fitted()
        return np.argsort(params.means[:, 0]).astype(np.int64)

    # --- EM internals ---------------------------------------------------------

    def _init_params(self, obs: Array, rng: np.random.Generator) -> HmmParams:
        centers = _kmeans_pp(obs, self.n_states, rng)
        # assign each point to nearest center for initial variances
        dists = np.linalg.norm(obs[:, None, :] - centers[None, :, :], axis=2)
        labels = np.argmin(dists, axis=1)
        d = obs.shape[1]
        variances = np.empty((self.n_states, d))
        for k in range(self.n_states):
            members = obs[labels == k]
            if members.shape[0] > 1:
                variances[k] = np.maximum(members.var(axis=0), self.var_floor)
            else:
                variances[k] = np.maximum(obs.var(axis=0), self.var_floor)
        startprob = np.full(self.n_states, 1.0 / self.n_states)
        transmat = np.full((self.n_states, self.n_states), 1.0 / self.n_states)
        return HmmParams(startprob, transmat, centers, variances)

    def _forward(self, params: HmmParams, log_b: Array) -> tuple[Array, float]:
        t_len, k = log_b.shape
        log_pi = np.log(params.startprob + 1e-300)
        log_a = np.log(params.transmat + 1e-300)
        log_alpha = np.empty((t_len, k))
        log_alpha[0] = log_pi + log_b[0]
        for t in range(1, t_len):
            # alpha[t,j] = b[t,j] * sum_i alpha[t-1,i] a[i,j]
            log_alpha[t] = log_b[t] + logsumexp(log_alpha[t - 1][:, None] + log_a, axis=0)
        ll = float(logsumexp(log_alpha[-1]))
        return log_alpha, ll

    def _backward(self, params: HmmParams, log_b: Array) -> Array:
        t_len, k = log_b.shape
        log_a = np.log(params.transmat + 1e-300)
        log_beta = np.zeros((t_len, k))  # log 1 at the final step
        for t in range(t_len - 2, -1, -1):
            log_beta[t] = logsumexp(log_a + (log_b[t + 1] + log_beta[t + 1])[None, :], axis=1)
        return log_beta

    def _posteriors(
        self,
        params: HmmParams,
        log_b: Array,
        log_alpha: Array,
        log_beta: Array,
        ll: float,
    ) -> tuple[Array, Array]:
        # gamma[t,k] = alpha[t,k] beta[t,k] / P(x)
        log_gamma = log_alpha + log_beta - ll
        gamma = np.exp(log_gamma)
        gamma /= gamma.sum(axis=1, keepdims=True)

        # xi summed over t: sum_t alpha[t,i] a[i,j] b[t+1,j] beta[t+1,j] / P(x)
        log_a = np.log(params.transmat + 1e-300)
        t_len, k = log_b.shape
        log_xi_acc = np.full((k, k), -np.inf)
        for t in range(t_len - 1):
            log_xi_t = (
                log_alpha[t][:, None]
                + log_a
                + log_b[t + 1][None, :]
                + log_beta[t + 1][None, :]
                - ll
            )
            log_xi_acc = np.logaddexp(log_xi_acc, log_xi_t)
        xi_sum = np.exp(log_xi_acc)
        return gamma, xi_sum

    def _m_step(self, obs: Array, gamma: Array, xi_sum: Array) -> HmmParams:
        d = obs.shape[1]
        nk = gamma.sum(axis=0)  # (K,)
        nk_safe = np.maximum(nk, 1e-300)

        startprob = gamma[0] / gamma[0].sum()

        denom = xi_sum.sum(axis=1, keepdims=True)
        transmat = xi_sum / np.maximum(denom, 1e-300)
        # rows with no mass fall back to uniform (degenerate but defined)
        empty = denom[:, 0] <= 1e-300
        transmat[empty] = 1.0 / self.n_states

        means = (gamma.T @ obs) / nk_safe[:, None]
        variances = np.empty((self.n_states, d))
        for k in range(self.n_states):
            diff = obs - means[k]
            variances[k] = (gamma[:, k][:, None] * diff * diff).sum(axis=0) / nk_safe[k]
        variances = np.maximum(variances, self.var_floor)
        return HmmParams(startprob, transmat, means, variances)

    def _score(self, params: HmmParams, obs: Array) -> float:
        log_b = _log_gaussian(obs, params.means, params.variances)
        _, ll = self._forward(params, log_b)
        return ll

    def _viterbi(self, params: HmmParams, obs: Array) -> NDArray[np.int64]:
        log_b = _log_gaussian(obs, params.means, params.variances)
        t_len, k = log_b.shape
        log_pi = np.log(params.startprob + 1e-300)
        log_a = np.log(params.transmat + 1e-300)
        delta = np.empty((t_len, k))
        psi = np.zeros((t_len, k), dtype=np.int64)
        delta[0] = log_pi + log_b[0]
        for t in range(1, t_len):
            scores = delta[t - 1][:, None] + log_a  # (i, j)
            psi[t] = np.argmax(scores, axis=0)
            delta[t] = log_b[t] + np.max(scores, axis=0)
        path = np.empty(t_len, dtype=np.int64)
        path[-1] = int(np.argmax(delta[-1]))
        for t in range(t_len - 2, -1, -1):
            path[t] = psi[t + 1, path[t + 1]]
        return path

    # --- guards ---------------------------------------------------------------

    def _require_fitted(self) -> HmmParams:
        if self.params_ is None:
            raise RuntimeError("model is not fitted; call fit() first")
        return self.params_

    def _require_params_match(self, x: Array) -> HmmParams:
        params = self._require_fitted()
        obs = _ensure_2d(x)
        if obs.shape[1] != params.n_features:
            raise ValueError(f"model has {params.n_features} features but input has {obs.shape[1]}")
        return params


# --- module helpers -----------------------------------------------------------


def _ensure_2d(x: Array) -> Array:
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2:
        raise ValueError("observations must be 1-D or 2-D")
    if not np.all(np.isfinite(arr)):
        raise ValueError("observations contain NaN or inf (drop warmup rows first)")
    return arr


def _log_gaussian(obs: Array, means: Array, variances: Array) -> Array:
    """Log diagonal-Gaussian density: ``(T, K)`` from ``(T, D)``, ``(K, D)``."""
    # log N = -0.5 * sum_d [ log(2 pi var) + (x-mu)^2 / var ]
    t_len, d = obs.shape
    k = means.shape[0]
    out = np.empty((t_len, k))
    for j in range(k):
        diff = obs - means[j]
        out[:, j] = -0.5 * (
            d * _LOG_2PI + np.sum(np.log(variances[j])) + np.sum(diff * diff / variances[j], axis=1)
        )
    return out


def _kmeans_pp(obs: Array, k: int, rng: np.random.Generator) -> Array:
    """k-means++ seeding followed by a few Lloyd iterations (deterministic
    given the generator). Provides a sensible, reproducible EM start."""
    n = obs.shape[0]
    first = int(rng.integers(n))
    centers = [obs[first]]
    for _ in range(1, k):
        d2 = np.min(np.stack([np.sum((obs - c) ** 2, axis=1) for c in centers], axis=1), axis=1)
        total = d2.sum()
        if total <= 0:
            centers.append(obs[int(rng.integers(n))])
            continue
        probs = d2 / total
        centers.append(obs[int(rng.choice(n, p=probs))])
    centers_arr = np.stack(centers)
    for _ in range(10):
        dists = np.linalg.norm(obs[:, None, :] - centers_arr[None, :, :], axis=2)
        labels = np.argmin(dists, axis=1)
        new = np.stack(
            [
                obs[labels == j].mean(axis=0) if np.any(labels == j) else centers_arr[j]
                for j in range(k)
            ]
        )
        if np.allclose(new, centers_arr):
            break
        centers_arr = new
    return centers_arr
