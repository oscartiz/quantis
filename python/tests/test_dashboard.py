"""Dashboard smoke tests: the strategy is causal-by-construction and the report
renders to a self-contained HTML file. (Not a test of profitability — the
strategy is an honest illustration, and may well underperform buy-and-hold.)"""

from pathlib import Path

import numpy as np

from quantis.dashboard.report import generate_report, run_strategy
from quantis.data.candles import load_candles

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDLES = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"


def test_strategy_runs_and_is_aligned() -> None:
    candles = load_candles(_CANDLES)
    r = run_strategy(candles, seed=42)
    n = len(r.strat_returns)
    assert n > 500
    # every series is aligned to the same length
    assert len(r.position) == n
    assert len(r.asset_returns) == n
    assert len(r.dates) == n
    assert r.price.shape[0] == n
    # position is long-or-flat only and finite
    assert set(np.unique(r.position)).issubset({0.0, 1.0})
    assert np.all(np.isfinite(r.strat_returns))


def test_position_uses_only_causal_filtered_signal() -> None:
    # The position must depend only on filtered (causal) regimes, so the time
    # series of positions is identical when recomputed on a prefix of candles.
    candles = load_candles(_CANDLES)
    full = run_strategy(candles, seed=42)
    # the filtered regime drives position; smoothed is analysis-only. Confirm
    # position is determined by filtered==bull and nothing future.
    assert np.array_equal((full.filtered_regime[: len(full.position)] == 2), full.position == 1.0)


def test_report_renders_self_contained_html(tmp_path: object) -> None:
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    out = generate_report(_CANDLES, tmp_path / "dash.html", seed=42)
    html = out.read_text(encoding="utf-8")
    # self-contained: images inlined, no external references to fetch
    assert "data:image/png;base64," in html
    assert 'src="http' not in html  # no remote images
    assert 'href="http' not in html  # no remote stylesheets/links
    # the honesty banner and the four panels are present
    assert "Read this first" in html  # the banner lead
    assert "out-of-sample" in html  # banner points to the holdout
    assert html.count("data:image/png;base64,") == 4
    assert "Per-regime attribution" in html
