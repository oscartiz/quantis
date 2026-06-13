"""Generate per-period charts of the regime model's trades on BTC.

Each chart is produced **walk-forward**: the HMM is fit only on data strictly
before the window (no look-ahead), then the causal strategy runs through the
window. For the first year (no prior data) the fit is in-sample and labelled so.

Output: docs/charts/*.png and a printed stats table.

Run (from repo root):
    uv run --project python python python/scripts/regime_charts.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from numpy.typing import NDArray

from quantis.data.candles import Candles, load_candles
from quantis.evaluation.metrics import max_drawdown, sharpe_ratio
from quantis.evaluation.regime_strategy import (
    causal_regime_returns,
    fit_regime_hmm,
)

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT = _REPO_ROOT / "docs" / "charts"
_TRADING_DAYS = 365
_WARMUP = 22  # bars borrowed before the window to warm the vol feature


@dataclass(frozen=True)
class WindowStats:
    name: str
    market: str
    dates: list[str]
    price: Array
    position: Array
    strat: Array
    hold: Array
    in_sample: bool

    @property
    def strat_return(self) -> float:
        return float(np.exp(np.sum(self.strat)) - 1.0)

    @property
    def hold_return(self) -> float:
        return float(np.exp(np.sum(self.hold)) - 1.0)

    @property
    def strat_sharpe(self) -> float:
        return sharpe_ratio(self.strat, _TRADING_DAYS)

    @property
    def hold_sharpe(self) -> float:
        return sharpe_ratio(self.hold, _TRADING_DAYS)

    @property
    def strat_maxdd(self) -> float:
        return max_drawdown(self.strat)

    @property
    def hold_maxdd(self) -> float:
        return max_drawdown(self.hold)

    @property
    def n_trades(self) -> int:
        # an entry (0->1) is one round-trip trade start
        return int(np.sum(np.diff(np.concatenate([[0.0], self.position])) > 0))

    @property
    def time_in_market(self) -> float:
        return float(np.mean(self.position))


def _idx_for_date(dates: list[str], ymd: str) -> int:
    for i, d in enumerate(dates):
        if d >= ymd:
            return i
    return len(dates)


def evaluate_window(
    candles: Candles,
    *,
    name: str,
    market: str,
    start_ymd: str,
    end_ymd: str | None,
    in_sample: bool,
    seed: int = 42,
) -> WindowStats:
    dates = candles.dates_iso()
    w0 = _idx_for_date(dates, start_ymd)
    w1 = len(dates) if end_ymd is None else _idx_for_date(dates, end_ymd)

    # Fit strictly on the past (or in-sample on the window for the first year).
    train_close = candles.close[:w0] if not in_sample else candles.close[w0:w1]
    model = fit_regime_hmm(train_close, seed=seed)

    # Evaluate causally on the window, borrowing warmup bars from before it.
    slice_start = max(0, w0 - _WARMUP)
    r = causal_regime_returns(model, candles.close[slice_start:w1])
    gidx = slice_start + r.candle_index
    keep = (gidx >= w0) & (gidx < w1)
    kept = gidx[keep]

    return WindowStats(
        name=name,
        market=market,
        dates=[dates[i] for i in kept],
        price=candles.close[kept],
        position=r.position[keep],
        strat=r.strat[keep],
        hold=r.hold[keep],
        in_sample=in_sample,
    )


def _date_ticks(ax: object, dates: list[str], n_ticks: int = 6) -> None:
    n = len(dates)
    if n == 0:
        return
    step = max(1, n // n_ticks)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)  # type: ignore[attr-defined]
    ax.set_xticklabels([dates[i] for i in ticks], rotation=30, ha="right", fontsize=8)  # type: ignore[attr-defined]


def render(stats: WindowStats, path: Path) -> None:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), height_ratios=[2, 1], sharex=True)
    x = np.arange(len(stats.price))

    # --- price with in-market shading and trade markers ---
    ax1.plot(x, stats.price, color="black", lw=1.1, label="BTC close", zorder=3)
    ax1.fill_between(
        x,
        stats.price.min(),
        stats.price.max(),
        where=stats.position > 0,
        color="#2ca02c",
        alpha=0.13,
        step="mid",
        label="long (in market)",
    )
    changes = np.diff(np.concatenate([[0.0], stats.position]))
    entries = np.flatnonzero(changes > 0)
    exits = np.flatnonzero(changes < 0)
    ax1.scatter(
        entries,
        stats.price[entries],
        marker="^",
        color="#1a7f1a",
        s=55,
        zorder=5,
        label="enter long",
    )
    ax1.scatter(
        exits, stats.price[exits], marker="v", color="#c01010", s=55, zorder=5, label="exit to flat"
    )
    tag = "  [IN-SAMPLE fit]" if stats.in_sample else "  [walk-forward: trained on prior data only]"
    ax1.set_title(f"BTC {stats.name} — {stats.market}{tag}", fontsize=11)
    ax1.set_ylabel("price (USD)")
    ax1.legend(loc="upper left", ncol=2, fontsize=8)
    ax1.grid(alpha=0.2)

    # --- equity curve vs buy & hold ---
    strat_eq = np.exp(np.cumsum(stats.strat))
    hold_eq = np.exp(np.cumsum(stats.hold))
    ax2.plot(x, strat_eq, color="#1f77b4", lw=1.4, label="regime strategy")
    ax2.plot(x, hold_eq, color="#7f7f7f", lw=1.0, ls="--", label="buy & hold")
    ax2.axhline(1.0, color="black", lw=0.5)
    ax2.set_ylabel("growth of $1")
    sub = (
        f"strat {stats.strat_return * 100:+.0f}% (Sharpe {stats.strat_sharpe:+.2f}, "
        f"maxDD {stats.strat_maxdd * 100:.0f}%)   vs   "
        f"hold {stats.hold_return * 100:+.0f}% (Sharpe {stats.hold_sharpe:+.2f}, "
        f"maxDD {stats.hold_maxdd * 100:.0f}%)   |   "
        f"{stats.n_trades} trades, {stats.time_in_market * 100:.0f}% in market"
    )
    ax2.set_title(sub, fontsize=9)
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(alpha=0.2)
    _date_ticks(ax2, stats.dates)

    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    _OUT.mkdir(parents=True, exist_ok=True)

    windows = [
        (
            "test-holdout",
            "the sealed out-of-sample test — a bear market",
            "2025-10-05",
            None,
            False,
        ),
        ("2023", "post-bear recovery (a strong uptrend year)", "2023-01-01", "2024-01-01", True),
        ("2024", "bull market with sharp corrections", "2024-01-01", "2025-01-01", False),
        ("2025", "blow-off top, then reversal", "2025-01-01", "2026-01-01", False),
        ("2026-ytd", "bear / downtrend", "2026-01-01", None, False),
    ]

    print(f"{'window':12} {'strat':>7} {'hold':>7} {'sharpe':>7} {'trades':>6} {'inmkt':>6}")
    for name, market, s, e, in_sample in windows:
        st = evaluate_window(
            candles, name=name, market=market, start_ymd=s, end_ymd=e, in_sample=in_sample
        )
        render(st, _OUT / f"btc-{name}.png")
        print(
            f"{name:12} {st.strat_return * 100:+6.0f}% {st.hold_return * 100:+6.0f}% "
            f"{st.strat_sharpe:+6.2f} {st.n_trades:6d} {st.time_in_market * 100:5.0f}%"
        )
    print(f"\ncharts written to {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
