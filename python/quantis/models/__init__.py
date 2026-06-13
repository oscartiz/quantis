"""Regime models: a hand-rolled Gaussian HMM (EM, validated against hmmlearn)
and Bayesian online changepoint detection (Adams-MacKay), each documenting the
market hypothesis it encodes."""

from quantis.models.hmm import GaussianHMM, HmmParams

__all__ = ["GaussianHMM", "HmmParams"]

