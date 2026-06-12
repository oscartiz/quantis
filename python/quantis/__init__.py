"""Quantis research layer: features, regime models, and honest evaluation.

The Rust core is exposed separately as :mod:`quantis_core` (built with maturin
from ``crates/python``); this package holds everything that is allowed to be
slow, exploratory, and visual.
"""

from quantis.config import (
    CrossValidationSpec,
    FeatureSpec,
    ModelSpec,
    ResearchConfig,
    load_research_config,
)

__version__ = "0.1.0"

__all__ = [
    "CrossValidationSpec",
    "FeatureSpec",
    "ModelSpec",
    "ResearchConfig",
    "__version__",
    "load_research_config",
]
