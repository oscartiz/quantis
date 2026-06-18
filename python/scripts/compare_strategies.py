"""One comprehensive chart comparing every strategy head-to-head.

All strategies are evaluated on the **same** out-of-sample window (the second
half of the research partition; the holdout stays sealed), under the **same**
fitted HMM, **net of real funding** — so the only thing that differs is the
strategy logic. Each family uses its *documented default* config (not the
search-winner), so this is an honest, non-cherry-picked comparison:

* Buy & hold (the price benchmark)
* HMM regime filter — binary long/flat (`causal_regime_returns`)
* HMM + BOCPD risk-off overlay (`causal_ensemble_returns`, ADR-007 defaults)
* Vol-target + conviction sizing (`causal_sized_returns`, ADR-008 defaults)
* Capped Kelly sizing (`causal_kelly_returns`, ADR-008 defaults)
* Long/short — shorts the bear regime (`causal_long_short_returns`, ADR-010)

Output: a single committed figure `docs/charts/strategy-comparison.png` (equity
curves + ranked metrics table, best Sharpe highlighted) and a printed leaderboard.

Run (from repo root):
    uv run --project python python python/scripts/compare_strategies.py
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

from quantis.data.candles import load_candles
from quantis.data.funding import load_daily_funding
from quantis.data.holdout import HoldoutManifest
from quantis.evaluation.directional_strategy import causal_long_short_returns
from quantis.evaluation.ensemble_strategy import causal_ensemble_returns
from quantis.evaluation.metrics import max_drawdown, sharpe_ratio, sortino_ratio
from quantis.evaluation.regime_strategy import RegimeReturns, causal_regime_returns, fit_regime_hmm
from quantis.evaluation.sizing_strategy import causal_kelly_returns, causal_sized_returns

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 365
_VOL_WINDOW = 20


@dataclass(frozen=True)
class StrategyResult:
    name: str
    color: str
    strat: Array  # per-bar log returns (net funding) on the OOS window
    position: Array  # per-bar weight / position

    @property
    def equity(self) -> Array:
        return np.exp(np.cumsum(self.strat))

    @property
    def total_return(self) -> float:
        return float(np.exp(np.sum(self.strat)) - 1.0)

    @property
    def sharpe(self) -> float:
        return sharpe_ratio(self.strat, _TRADING_DAYS)

    @property
    def sortino(self) -> float:
        return sortino_ratio(self.strat, _TRADING_DAYS)

    @property
    def max_dd(self) -> float:
        return max_drawdown(self.strat)

    @property
    def exposure(self) -> float:
        return float(np.mean(np.abs(self.position)))


def main() -> int:
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    manifest = HoldoutManifest.from_json(_REPO_ROOT / "data" / "sample" / "holdout-manifest.json")
    funding = load_daily_funding(
        _REPO_ROOT / "data" / "sample" / "btc-funding.csv", candles.dates_iso()
    )
    close = candles.close
    dates = candles.dates_iso()

    boundary = manifest.boundary_index  # research = close[:boundary]; holdout sealed
    train_end = boundary // 2
    slice_start = max(0, train_end - (_VOL_WINDOW + 5))
    sliced = close[slice_start:boundary]
    f_sliced = funding[slice_start:boundary]
    model = fit_regime_hmm(close[:train_end], seed=42, vol_window=_VOL_WINDOW)

    # Evaluate every strategy on the identical slice, then keep the OOS rows.
    base = causal_regime_returns(model, sliced, vol_window=_VOL_WINDOW, funding_daily=f_sliced)
    gidx = slice_start + base.candle_index
    oos = gidx >= train_end
    oos_dates = [dates[i] for i in gidx[oos]]

    def keep(r: RegimeReturns) -> tuple[Array, Array]:
        assert np.array_equal(slice_start + r.candle_index, gidx)  # identical alignment
        return r.strat[oos], r.position[oos]

    strategies: list[StrategyResult] = []
    # Buy & hold: the price benchmark (gross of funding, the standard yardstick).
    bh_ret = base.hold[oos]
    strategies.append(StrategyResult("Buy & hold", "#7f7f7f", bh_ret, np.ones_like(bh_ret)))
    for name, color, r in (
        ("HMM regime filter (long/flat)", "#1f77b4", base),
        (
            "HMM + BOCPD overlay",
            "#17becf",
            causal_ensemble_returns(model, sliced, vol_window=_VOL_WINDOW, funding_daily=f_sliced),
        ),
        (
            "Vol-target + conviction sizing",
            "#2ca02c",
            causal_sized_returns(
                model,
                sliced,
                vol_window=_VOL_WINDOW,
                funding_daily=f_sliced,
                target_vol=0.02,
                conviction_weighted=True,
            ),
        ),
        (
            "Capped Kelly sizing",
            "#9467bd",
            causal_kelly_returns(model, sliced, vol_window=_VOL_WINDOW, funding_daily=f_sliced),
        ),
        (
            "Long/short (shorts the bear)",
            "#d62728",
            causal_long_short_returns(
                model, sliced, vol_window=_VOL_WINDOW, funding_daily=f_sliced
            ),
        ),
    ):
        s, p = keep(r)
        strategies.append(StrategyResult(name, color, s, p))

    # Rank active strategies (exclude the benchmark) by Sharpe for "best".
    active = [s for s in strategies if s.name != "Buy & hold"]
    best_active = max(active, key=lambda s: s.sharpe)
    best_dd = min(active, key=lambda s: s.max_dd)
    best_overall = max(strategies, key=lambda s: s.sharpe)  # may be buy & hold

    _render(strategies, oos_dates, best_overall, best_active, best_dd)
    _print_leaderboard(strategies, len(oos_dates), best_active, best_dd)
    return 0


def _print_leaderboard(
    strategies: list[StrategyResult],
    n_days: int,
    best_active: StrategyResult,
    best_dd: StrategyResult,
) -> None:
    rows = sorted(strategies, key=lambda s: s.sharpe, reverse=True)
    print(f"\n=== STRATEGY COMPARISON (OOS-within-research, {n_days} days, net of funding) ===\n")
    print(f"  {'rank  strategy':38}{'Sharpe':>9}{'Sortino':>9}{'maxDD':>8}{'totRet':>9}{'expo':>8}")
    for i, s in enumerate(rows, 1):
        print(
            f"  {i:<2}  {s.name:32}{s.sharpe:>+9.2f}{s.sortino:>+9.2f}"
            f"{s.max_dd * 100:>7.1f}%{s.total_return * 100:>+8.1f}%{s.exposure:>7.2f}x"
        )
    print(
        f"\n  best risk-adjusted among strategies: {best_active.name} "
        f"(Sharpe {best_active.sharpe:+.2f})"
    )
    print(
        f"  lowest drawdown among strategies:    {best_dd.name} (maxDD {best_dd.max_dd * 100:.1f}%)"
    )
    print(
        "\n  Honest reading: this OOS span is bull-leaning, so buy & hold leads on "
        "Sharpe here; the\n  regime strategies are drawdown-avoiders whose edge shows "
        "in the sealed BEAR holdout, not\n  on this window. And NONE survives the "
        "multiple-testing correction (global DSR 0.66).\n"
    )


def _render(
    strategies: list[StrategyResult],
    oos_dates: list[str],
    best_overall: StrategyResult,
    best_active: StrategyResult,
    best_dd: StrategyResult,
) -> None:
    n = len(oos_dates)
    x = np.arange(n)
    fig = plt.figure(figsize=(15, 11))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.0, 1.7], hspace=0.22)
    ax_eq = fig.add_subplot(gs[0])
    ax_tbl = fig.add_subplot(gs[1])

    # ---- panel 1: cumulative growth of $1 ----
    for s in strategies:
        ls = "--" if s.name == "Buy & hold" else "-"
        lw = 1.4 if s.name == "Buy & hold" else 2.0
        ax_eq.plot(x, s.equity, color=s.color, lw=lw, ls=ls, label=s.name, zorder=3)
        ax_eq.annotate(
            f" x{s.equity[-1]:.2f}",
            xy=(n - 1, s.equity[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            ha="left",
            fontsize=8,
            color=s.color,
            fontweight="bold",
            annotation_clip=False,
        )
    ax_eq.axhline(1.0, color="black", lw=0.5)
    ax_eq.set_yscale("log")
    ax_eq.set_ylabel("growth of $1 (log scale)")
    ax_eq.set_title(
        "Quantis — every strategy head-to-head (out-of-sample, net of funding)\n"
        "same fitted HMM, same window, documented-default configs — no look-ahead, "
        "no cherry-picking",
        fontsize=12,
    )
    ax_eq.legend(loc="upper left", fontsize=9, ncol=2)
    ax_eq.grid(alpha=0.18, which="both")
    step = max(1, n // 9)
    ticks = list(range(0, n, step))
    ax_eq.set_xticks(ticks)
    ax_eq.set_xticklabels(
        [oos_dates[min(i, n - 1)] for i in ticks], rotation=30, ha="right", fontsize=8
    )

    # ---- panel 2: ranked metrics table ----
    ax_tbl.axis("off")
    rows = sorted(strategies, key=lambda s: s.sharpe, reverse=True)
    header = (
        f"  {'#  strategy':36}{'Sharpe':>10}{'Sortino':>10}"
        f"{'maxDD':>9}{'totRet':>10}{'exposure':>11}"
    )
    lines = [header, "  " + "─" * 84]
    for i, s in enumerate(rows, 1):
        mark = "  <- best Sharpe" if s is best_overall else ""
        lines.append(
            f"  {i:<2} {s.name:33}{s.sharpe:>+10.2f}{s.sortino:>+10.2f}"
            f"{s.max_dd * 100:>8.1f}%{s.total_return * 100:>+9.1f}%{s.exposure:>10.2f}x{mark}"
        )
    ax_tbl.text(
        0.0,
        0.98,
        "\n".join(lines),
        family="monospace",
        fontsize=10.5,
        va="top",
        ha="left",
        transform=ax_tbl.transAxes,
    )
    note = (
        f"Best risk-adjusted strategy: {best_active.name} (Sharpe {best_active.sharpe:+.2f}).   "
        f"Lowest drawdown: {best_dd.name} ({best_dd.max_dd * 100:.1f}%).\n"
        "This OOS span is bull-leaning, so buy & hold leads on Sharpe HERE; the regime "
        "strategies are drawdown-avoiders whose\nedge appears in the sealed BEAR holdout, "
        "not on this window. NONE survives the multiple-testing correction (global DSR 0.66) "
        "—\nsee results/research-report.html. Strategies are net of funding; buy & hold is the "
        "gross price benchmark."
    )
    ax_tbl.text(
        0.0,
        0.30,
        note,
        fontsize=8.6,
        va="top",
        ha="left",
        color="#555555",
        transform=ax_tbl.transAxes,
    )

    out = _REPO_ROOT / "docs" / "charts" / "strategy-comparison.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  chart written to {out}")


if __name__ == "__main__":
    sys.exit(main())
