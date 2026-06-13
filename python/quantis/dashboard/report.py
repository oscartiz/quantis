"""Static, self-contained HTML research dashboard.

Renders one offline HTML file (PNGs inlined as base64 — no server, no external
assets) from a candle dataset: a regime-overlaid price chart, the strategy
equity curve vs. buy-and-hold, rolling Sharpe/Sortino, drawdown, exposure, and
a per-regime trade-attribution table.

Honesty is built into the construction, not just the prose:

- The price overlay shows the HMM's **smoothed** regimes — labelled non-causal,
  analysis-only.
- The equity curve is driven by the **filtered** (causal) regime signal, so it
  is a signal a strategy could actually have traded. Both come from the same
  in-sample fit, so the curve is illustrative; the genuinely out-of-sample
  number is the one-shot holdout (`scripts/evaluate_holdout.py`).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: render to PNG, never open a window
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from numpy.typing import NDArray

from quantis.data.candles import Candles, load_candles
from quantis.evaluation.metrics import max_drawdown, sharpe_ratio, sortino_ratio
from quantis.evaluation.regime_strategy import (
    DEFAULT_COST_BPS,
    causal_regime_returns,
    fit_regime_hmm,
    ordered_regimes,
    regime_features,
)

Array = NDArray[np.float64]

_TRADING_DAYS = 365  # crypto trades every day
_REGIME_COLORS = ["#d62728", "#7f7f7f", "#2ca02c"]  # bear, chop, bull
_REGIME_NAMES = ["bear", "chop", "bull"]


@dataclass(frozen=True)
class StrategyResult:
    """Outputs of the causal regime strategy over the warmed candle range."""

    dates: list[str]
    price: Array
    smoothed_regime: NDArray[np.int64]  # ordered 0=bear..2=bull (non-causal)
    filtered_regime: NDArray[np.int64]  # ordered (causal)
    position: Array  # held over the NEXT period (causal)
    strat_returns: Array  # realized strategy log returns
    asset_returns: Array  # buy-and-hold log returns over the same range
    cost_bps: float


def run_strategy(
    candles: Candles, *, seed: int = 42, cost_bps: float = DEFAULT_COST_BPS
) -> StrategyResult:
    """Fit the HMM (in-sample) and run the causal filtered-regime strategy, plus
    the smoothed regimes for the (non-causal) analysis overlay."""
    model = fit_regime_hmm(candles.close, seed=seed)
    r = causal_regime_returns(model, candles.close, cost_bps=cost_bps)

    # Smoothed regimes for the overlay, aligned to the same rows as the returns.
    fm = regime_features(candles.close)
    valid = np.flatnonzero(fm.valid)
    smoothed = ordered_regimes(model, model.predict_proba(fm.values[valid]))
    filtered = ordered_regimes(model, model.filter_proba(fm.values[valid]))
    n = r.strat.shape[0]
    all_dates = candles.dates_iso()

    return StrategyResult(
        dates=[all_dates[i] for i in r.candle_index],
        price=candles.close[r.candle_index],
        smoothed_regime=smoothed[:n],
        filtered_regime=filtered[:n],
        position=r.position,
        strat_returns=r.strat,
        asset_returns=r.hold,
        cost_bps=cost_bps,
    )


def _fig_to_base64(fig: Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _rolling(values: Array, window: int, fn: object) -> Array:
    out = np.full(values.shape[0], np.nan)
    for t in range(window, values.shape[0]):
        out[t] = fn(values[t - window : t])  # type: ignore[operator]
    return out


def _plot_price_regimes(r: StrategyResult) -> str:
    fig, ax = plt.subplots(figsize=(11, 3.6))
    x = np.arange(len(r.price))
    ax.plot(x, r.price, color="black", lw=0.9, label="BTC close")
    # shade by smoothed regime (analysis overlay)
    for regime in range(3):
        ax.fill_between(
            x,
            r.price.min(),
            r.price.max(),
            where=r.smoothed_regime == regime,
            color=_REGIME_COLORS[regime],
            alpha=0.18,
            step="mid",
        )
    ax.set_yscale("log")
    ax.set_title("BTC price with HMM smoothed regimes (non-causal, analysis only)")
    ax.set_ylabel("price (log)")
    handles = [Rectangle((0, 0), 1, 1, color=_REGIME_COLORS[i], alpha=0.4) for i in range(3)]
    ax.legend(handles, _REGIME_NAMES, loc="upper left", ncol=3, fontsize=8)
    _date_ticks(ax, r.dates)
    return _fig_to_base64(fig)


def _plot_equity(r: StrategyResult) -> str:
    fig, ax = plt.subplots(figsize=(11, 3.6))
    x = np.arange(len(r.strat_returns))
    strat = np.exp(np.cumsum(r.strat_returns))
    hold = np.exp(np.cumsum(r.asset_returns))
    ax.plot(x, strat, color="#1f77b4", lw=1.2, label="causal regime strategy")
    ax.plot(x, hold, color="#7f7f7f", lw=1.0, ls="--", label="buy & hold")
    ax.set_title("Equity curve (growth of 1, net of costs) — in-sample illustration")
    ax.set_ylabel("equity")
    ax.legend(loc="upper left", fontsize=8)
    _date_ticks(ax, r.dates)
    return _fig_to_base64(fig)


def _plot_rolling_risk(r: StrategyResult) -> str:
    fig, ax = plt.subplots(figsize=(11, 3.2))
    window = 90
    sharpe = _rolling(r.strat_returns, window, lambda v: sharpe_ratio(v, _TRADING_DAYS))
    sortino = _rolling(r.strat_returns, window, lambda v: sortino_ratio(v, _TRADING_DAYS))
    x = np.arange(len(r.strat_returns))
    ax.plot(x, sharpe, color="#1f77b4", lw=1.0, label=f"rolling Sharpe ({window}d, ann.)")
    ax.plot(x, sortino, color="#ff7f0e", lw=1.0, label=f"rolling Sortino ({window}d, ann.)")
    ax.axhline(0.0, color="black", lw=0.6)
    ax.set_title("Rolling risk-adjusted return")
    ax.legend(loc="upper left", fontsize=8)
    _date_ticks(ax, r.dates)
    return _fig_to_base64(fig)


def _plot_drawdown_exposure(r: StrategyResult) -> str:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 4.4), sharex=True)
    equity = np.exp(np.cumsum(r.strat_returns))
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    x = np.arange(len(r.strat_returns))
    ax1.fill_between(x, dd, 0.0, color="#d62728", alpha=0.5)
    ax1.set_title("Drawdown")
    ax1.set_ylabel("drawdown")
    ax2.fill_between(x, r.position, 0.0, color="#2ca02c", alpha=0.5, step="mid")
    ax2.set_title("Exposure (position: 1 = long, 0 = flat)")
    ax2.set_ylabel("position")
    _date_ticks(ax2, r.dates)
    return _fig_to_base64(fig)


def _date_ticks(ax: object, dates: list[str]) -> None:
    n = len(dates)
    if n == 0:
        return
    step = max(1, n // 8)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)  # type: ignore[attr-defined]
    ax.set_xticklabels([dates[i] for i in ticks], rotation=30, ha="right", fontsize=7)  # type: ignore[attr-defined]


def _attribution_table(r: StrategyResult) -> str:
    rows = []
    total = float(np.sum(r.strat_returns))
    for regime in range(3):
        mask = r.smoothed_regime[: len(r.strat_returns)] == regime
        days = int(np.sum(mask))
        contrib = float(np.sum(r.strat_returns[mask])) if days else 0.0
        mean = float(np.mean(r.strat_returns[mask])) if days else 0.0
        share = (contrib / total * 100.0) if total != 0 else 0.0
        rows.append(
            f"<tr><td>{_REGIME_NAMES[regime]}</td><td>{days}</td>"
            f"<td>{mean * 1e4:.1f}</td><td>{contrib:+.3f}</td><td>{share:+.0f}%</td></tr>"
        )
    return (
        "<table><thead><tr><th>regime (smoothed)</th><th>days</th>"
        "<th>mean daily ret (bps)</th><th>total log-ret contrib</th>"
        "<th>share of PnL</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _summary_stats(r: StrategyResult) -> dict[str, str]:
    strat_sharpe = sharpe_ratio(r.strat_returns, _TRADING_DAYS)
    hold_sharpe = sharpe_ratio(r.asset_returns, _TRADING_DAYS)
    return {
        "Span": f"{r.dates[0]} to {r.dates[-1]} ({len(r.strat_returns)} days)",
        "Strategy Sharpe (ann.)": f"{strat_sharpe:.2f}",
        "Buy & hold Sharpe (ann.)": f"{hold_sharpe:.2f}",
        "Strategy max drawdown": f"{max_drawdown(r.strat_returns) * 100:.1f}%",
        "Buy & hold max drawdown": f"{max_drawdown(r.asset_returns) * 100:.1f}%",
        "Time in market": f"{np.mean(r.position) * 100:.0f}%",
        "Round-trip cost": f"{r.cost_bps:.1f} bps",
    }


def generate_report(candles_path: str | Path, output_path: str | Path, *, seed: int = 42) -> Path:
    """Render the dashboard for ``candles_path`` to a single HTML file."""
    candles = load_candles(candles_path)
    r = run_strategy(candles, seed=seed)

    panels = {
        "Price & regimes": _plot_price_regimes(r),
        "Equity curve": _plot_equity(r),
        "Rolling risk": _plot_rolling_risk(r),
        "Drawdown & exposure": _plot_drawdown_exposure(r),
    }
    stats = _summary_stats(r)
    stats_html = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in stats.items())
    imgs_html = "".join(
        f'<h2>{title}</h2><img src="data:image/png;base64,{b64}" />'
        for title, b64 in panels.items()
    )

    html = _TEMPLATE.format(
        stats=stats_html,
        images=imgs_html,
        attribution=_attribution_table(r),
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<title>Quantis — Research Dashboard</title>
<style>
 body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1000px;
        margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
 h1 {{ margin-bottom: 0.2rem; }} h2 {{ margin-top: 1.6rem; font-size: 1.05rem; }}
 img {{ width: 100%; border: 1px solid #eee; border-radius: 4px; }}
 table {{ border-collapse: collapse; margin: 0.5rem 0; font-size: 0.9rem; }}
 th, td {{ border: 1px solid #ddd; padding: 4px 10px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 .banner {{ background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px;
            padding: 0.8rem 1rem; font-size: 0.9rem; }}
 .banner b {{ color: #b26a00; }}
</style></head><body>
<h1>Quantis — Research Dashboard</h1>
<p class="banner"><b>Read this first.</b> The regime overlay uses the HMM's
<i>smoothed</i> states, which depend on future data and are for <b>analysis
only</b> — never a trading signal. The equity curve uses the <i>filtered</i>
(causal) signal, but under parameters fit <b>in-sample</b>, so it is an
illustration of the workflow, not evidence of edge. The genuinely
out-of-sample result is the one-shot holdout evaluation. See
<code>docs/losing-money.md</code> and <code>docs/statistical-honesty.md</code>.</p>
<h2>Summary</h2>
<table><tbody>{stats}</tbody></table>
{images}
<h2>Per-regime attribution</h2>
<p>Realized strategy returns grouped by the (smoothed) regime they occurred in.</p>
{attribution}
</body></html>
"""
