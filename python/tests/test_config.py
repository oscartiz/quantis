"""Validation behavior of the research config schema."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from quantis.config import ResearchConfig, load_research_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_raw() -> dict[str, Any]:
    return {
        "seed": 7,
        "instrument": "BTC",
        "bar_interval": "1m",
        "features": [{"name": "log_return", "params": {"lag": 1}}],
        "model": {"name": "gaussian_hmm", "params": {"n_states": 3}},
        "cv": {"scheme": "walk_forward", "n_splits": 5, "embargo_bars": 60},
    }


def test_example_config_is_valid() -> None:
    cfg = load_research_config(_REPO_ROOT / "config" / "research.example.yaml")
    assert cfg.model.name == "gaussian_hmm"
    assert cfg.seed == 42
    assert cfg.cv.embargo_bars > 0


def test_unknown_top_level_field_is_rejected() -> None:
    raw = _valid_raw()
    raw["surprise"] = True
    with pytest.raises(ValidationError, match="surprise"):
        ResearchConfig.model_validate(raw)


def test_unknown_nested_field_is_rejected() -> None:
    raw = _valid_raw()
    raw["cv"]["lookahead"] = 1
    with pytest.raises(ValidationError, match="lookahead"):
        ResearchConfig.model_validate(raw)


def test_negative_embargo_is_rejected() -> None:
    raw = _valid_raw()
    raw["cv"]["embargo_bars"] = -1
    with pytest.raises(ValidationError):
        ResearchConfig.model_validate(raw)


def test_at_least_one_feature_is_required() -> None:
    raw = _valid_raw()
    raw["features"] = []
    with pytest.raises(ValidationError):
        ResearchConfig.model_validate(raw)


def test_unknown_model_name_is_rejected() -> None:
    raw = _valid_raw()
    raw["model"]["name"] = "secret_alpha"
    with pytest.raises(ValidationError):
        ResearchConfig.model_validate(raw)
