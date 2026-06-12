"""Research configuration: YAML on disk, validated by pydantic.

Mirrors the philosophy of the Rust engine config (crates/core/src/config.rs):
unknown fields are errors, every knob lives in config rather than code, and
every run is seeded. ``config/research.example.yaml`` is parsed by the test
suite, so the example can never drift from this schema.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

ParamValue = float | int | str | bool


class _StrictModel(BaseModel):
    """Base for all config models: unknown fields are errors, values frozen."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FeatureSpec(_StrictModel):
    """One feature in the pipeline, fully determined by ``name`` + ``params``."""

    name: str = Field(min_length=1)
    params: dict[str, ParamValue] = Field(default_factory=dict)


class ModelSpec(_StrictModel):
    """Which regime model to fit and its hyperparameters."""

    name: Literal["gaussian_hmm", "bocpd"]
    params: dict[str, ParamValue] = Field(default_factory=dict)


class CrossValidationSpec(_StrictModel):
    """Time-series CV scheme; the embargo guards against leakage at fold edges."""

    scheme: Literal["walk_forward", "purged_kfold"]
    n_splits: int = Field(ge=2)
    embargo_bars: int = Field(ge=0)


class ResearchConfig(_StrictModel):
    """A fully seeded, self-contained definition of one research run."""

    seed: int
    instrument: str = Field(min_length=1)
    bar_interval: Literal["1m", "5m", "15m", "1h"]
    features: list[FeatureSpec] = Field(min_length=1)
    model: ModelSpec
    cv: CrossValidationSpec


def load_research_config(path: str | Path) -> ResearchConfig:
    """Load and validate a research config from a YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ResearchConfig.model_validate(raw)
