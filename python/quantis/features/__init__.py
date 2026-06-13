"""Feature engineering: a config-driven pipeline where every feature is fully
determined by its name and parameters in YAML — no magic numbers in code, no
silently shifting windows. Features are causal by construction and guarded by
a prefix-consistency leakage check."""

from quantis.features.pipeline import (
    FeatureMatrix,
    LeakageError,
    assert_causal,
    available_features,
    build_features,
    is_causal,
)

__all__ = [
    "FeatureMatrix",
    "LeakageError",
    "assert_causal",
    "available_features",
    "build_features",
    "is_causal",
]
