"""One consolidated performance chart over the full available BTC history.

This stitches the repo's *existing* causal machinery into a single picture, with
no new look-ahead and no re-tuning:

* One regime HMM is fit on the RESEARCH partition only (exactly as
  ``scripts/evaluate_holdout.py`` does — ``fit_regime_hmm(load_research(...))``).
* The causal strategy (``causal_regime_returns``, filtered/no-look-ahead signal)
  is run over the research partition and, separately, over the sealed holdout
  revealed via the committed manifest. The two equity curves are compounded into
  one continuous line.
* The boundary is the committed ``holdout-manifest.json`` (index 1008). Left of
  it the model has *seen* the data (in-sample / exploratory); right of it is the
  pre-registered holdout, evaluated once. They are shaded and labelled
  differently because they are NOT equivalent evidence.

Every parameter is a repo default (seed 42, 5 bps round-trip, 20-bar vol window,
3 states, 365 trading days). Nothing here is invented or tuned.

The holdout segment reproduces ``results/holdout-evaluation.json`` exactly; the
script asserts this before drawing, so the chart cannot silently drift from the
sealed number.

Run (from repo root):
    uv run --project python python python/scripts/full_history_chart.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from numpy.typing import NDArray

from quantis.data.candles import load_candles
from quantis.data.holdout import ACKNOWLEDGEMENT, HoldoutManifest, load_research, reveal_holdout
from quantis.evaluation.metrics import max_drawdown, sharpe_ratio
from quantis.evaluation.regime_strategy import RegimeReturns, causal_regime_returns, fit_regime_hmm

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 365


@dataclass(frozen=True)
class Segment:
    """One contiguous evaluated span (in-sample or holdout)."""

    label: str
    dates: list[str]
    price: Array
    position: Array
    strat: Array  # strategy log returns
    hold: Array  # buy-and-hold log returns

    @property
    def total_return(self) -> float:
        return float(np.exp(np.sum(self.strat)) - 1.0)

    @property
    def hold_return(self) -> float:
        return float(np.exp(np.sum(self.hold)) - 1.0)

    @property
    def n_trades(self) -> int:
        return int(np.sum(np.diff(np.concatenate([[0.0], self.position])) > 0))

    @property
    def time_in_market(self) -> float:
        return float(np.mean(self.position))


def _segment(label: str, all_dates: list[str], offset: int, r: RegimeReturns) -> Segment:
    gidx = offset + r.candle_index
    return Segment(
        label=label,
        dates=[all_dates[i] for i in gidx],
        price=np.array([_CLOSE[i] for i in gidx], dtype=np.float64),
        position=r.position,
        strat=r.strat,
        hold=r.hold,
    )


# populated in main(); module-level so the tiny _segment helper can read prices
_CLOSE: Array = np.empty(0)


def _row(name: str, strat_vals: list[str], hold_vals: list[str]) -> str:
    cells = "".join(f"{v:>11}" for v in strat_vals) + "  " + "".join(f"{v:>11}" for v in hold_vals)
    return f"  {name:<26}{cells}"


def _stats_block(seg: Segment) -> tuple[list[str], list[str]]:
    strat = [
        f"{seg.total_return * 100:+.0f}%",
        f"{sharpe_ratio(seg.strat, _TRADING_DAYS):+.2f}",
        f"{max_drawdown(seg.strat) * 100:.0f}%",
        f"{seg.time_in_market * 100:.0f}%",
        f"{seg.n_trades}",
    ]
    hold = [
        f"{seg.hold_return * 100:+.0f}%",
        f"{sharpe_ratio(seg.hold, _TRADING_DAYS):+.2f}",
        f"{max_drawdown(seg.hold) * 100:.0f}%",
        "100%",
        "1",
    ]
    return strat, hold


def _full_segment(in_sample: Segment, holdout: Segment) -> Segment:
    return Segment(
        label="full history",
        dates=in_sample.dates + holdout.dates,
        price=np.concatenate([in_sample.price, holdout.price]),
        position=np.concatenate([in_sample.position, holdout.position]),
        strat=np.concatenate([in_sample.strat, holdout.strat]),
        hold=np.concatenate([in_sample.hold, holdout.hold]),
    )


def _verify_holdout(holdout: Segment) -> None:
    """Refuse to draw if the holdout segment drifts from the sealed artifact."""
    committed = json.loads(
        (_REPO_ROOT / "results" / "holdout-evaluation.json").read_text(encoding="utf-8")
    )
    checks = {
        "total_return": (holdout.total_return, committed["strategy_total_return"]),
        "sharpe": (sharpe_ratio(holdout.strat, _TRADING_DAYS), committed["holdout_sharpe"]),
        "max_drawdown": (max_drawdown(holdout.strat), committed["holdout_max_drawdown"]),
        "time_in_market": (holdout.time_in_market, committed["time_in_market"]),
        "days": (float(len(holdout.strat)), float(committed["holdout_days"])),
    }
    for name, (got, want) in checks.items():
        if not np.isclose(got, want, rtol=1e-9, atol=1e-9):
            raise AssertionError(
                f"holdout segment {name}={got} != sealed artifact {want}; the chart "
                "would not match the pre-registered number — aborting."
            )
    print("  holdout segment reproduces results/holdout-evaluation.json exactly ✓")


def _date_ticks(ax: object, dates: list[str], boundary: int, n_ticks: int = 9) -> None:
    n = len(dates)
    step = max(1, n // n_ticks)
    ticks = sorted({*range(0, n, step), boundary})
    ax.set_xticks(ticks)  # type: ignore[attr-defined]
    ax.set_xticklabels(  # type: ignore[attr-defined]
        [dates[min(i, n - 1)] for i in ticks], rotation=30, ha="right", fontsize=8
    )


def render(in_sample: Segment, holdout: Segment, full: Segment, out: Path) -> None:
    boundary = len(in_sample.strat)  # first holdout row in the concatenated index
    n = len(full.strat)
    x = np.arange(n)

    fig = plt.figure(figsize=(15, 10.5))
    gs = fig.add_gridspec(3, 1, height_ratios=[3.0, 2.4, 1.35], hspace=0.30)
    ax_price = fig.add_subplot(gs[0])
    ax_eq = fig.add_subplot(gs[1], sharex=ax_price)
    ax_tbl = fig.add_subplot(gs[2])

    # ---- region tints: in-sample (left) vs sealed holdout (right) ----
    for ax in (ax_price, ax_eq):
        ax.axvspan(0, boundary, color="#9e9e9e", alpha=0.06)
        ax.axvspan(boundary, n, color="#1f77b4", alpha=0.07)
        ax.axvline(boundary, color="#1f77b4", lw=1.4, ls="--", alpha=0.8)

    # ================= panel 1: price + in-market shading + trades =================
    ax_price.plot(x, full.price, color="black", lw=1.0, label="BTC close", zorder=3)
    ax_price.fill_between(
        x,
        full.price.min(),
        full.price.max(),
        where=full.position > 0,
        color="#2ca02c",
        alpha=0.16,
        step="mid",
        label="long (in market)",
        zorder=1,
    )
    changes = np.diff(np.concatenate([[0.0], full.position]))
    entries = np.flatnonzero(changes > 0)
    exits = np.flatnonzero(changes < 0)
    ax_price.scatter(
        entries,
        full.price[entries],
        marker="^",
        color="#1a7f1a",
        s=42,
        zorder=5,
        label="enter long",
    )
    ax_price.scatter(
        exits,
        full.price[exits],
        marker="v",
        color="#c01010",
        s=42,
        zorder=5,
        label="exit to flat",
    )
    ax_price.set_yscale("log")
    ax_price.set_ylabel("BTC price (USD, log)")
    ax_price.set_title(
        "Quantis regime strategy vs. buy & hold — full BTC daily history "
        "(2023-01-01 to 2026-06-13)\n"
        "causal filtered-HMM signal, no look-ahead; model fit on the research partition only",
        fontsize=12,
    )
    ax_price.legend(loc="upper left", ncol=2, fontsize=8)
    ax_price.grid(alpha=0.18, which="both")
    _ymax = full.price.max()
    ax_price.text(
        boundary / 2,
        _ymax * 0.96,
        "IN-SAMPLE / EXPLORATORY\n(model was fit on this span)",
        ha="center",
        va="top",
        fontsize=9,
        color="#555555",
        bbox={"boxstyle": "round", "fc": "white", "ec": "#bbbbbb", "alpha": 0.85},
    )
    ax_price.text(
        boundary + (n - boundary) / 2,
        _ymax * 0.96,
        "SEALED HOLDOUT\n(pre-registered, evaluated once)",
        ha="center",
        va="top",
        fontsize=9,
        color="#1f77b4",
        bbox={"boxstyle": "round", "fc": "white", "ec": "#1f77b4", "alpha": 0.9},
    )

    # ================= panel 2: cumulative equity vs buy & hold =================
    strat_eq = np.exp(np.cumsum(full.strat))
    hold_eq = np.exp(np.cumsum(full.hold))
    ax_eq.plot(x, strat_eq, color="#1f77b4", lw=1.8, label="regime strategy", zorder=4)
    ax_eq.plot(x, hold_eq, color="#7f7f7f", lw=1.2, ls="--", label="buy & hold", zorder=3)
    ax_eq.axhline(1.0, color="black", lw=0.5)
    ax_eq.set_yscale("log")
    ax_eq.set_ylabel("growth of $1 (log)")
    ax_eq.legend(loc="upper left", fontsize=9)
    ax_eq.grid(alpha=0.18, which="both")
    _date_ticks(ax_eq, full.dates, boundary)

    # endpoint value labels so the headline is readable without the table
    ax_eq.annotate(
        f"strategy  x{strat_eq[-1]:.2f}  ({full.total_return * 100:+.0f}%)",
        xy=(n - 1, strat_eq[-1]),
        xytext=(8, 0),
        textcoords="offset points",
        va="center",
        ha="left",
        fontsize=9,
        color="#1f77b4",
        fontweight="bold",
        annotation_clip=False,
    )
    ax_eq.annotate(
        f"buy & hold  x{hold_eq[-1]:.2f}  ({full.hold_return * 100:+.0f}%)",
        xy=(n - 1, hold_eq[-1]),
        xytext=(8, 0),
        textcoords="offset points",
        va="center",
        ha="left",
        fontsize=9,
        color="#5f5f5f",
        annotation_clip=False,
    )
    # callout on the holdout divergence — the whole point of the strategy
    ho_mid = boundary + (n - boundary) // 2
    ax_eq.annotate(
        f"SEALED HOLDOUT (bear)\nstrategy {holdout.total_return * 100:+.0f}% — "
        f"holds cash through the crash\nbuy & hold {holdout.hold_return * 100:+.0f}%",
        xy=(ho_mid, hold_eq[ho_mid]),
        xytext=(boundary - 30, hold_eq.min() * 1.05),
        ha="right",
        va="bottom",
        fontsize=8.5,
        color="#1f77b4",
        bbox={"boxstyle": "round", "fc": "white", "ec": "#1f77b4", "alpha": 0.9},
        arrowprops={"arrowstyle": "->", "color": "#1f77b4", "lw": 1.0},
    )

    # ================= panel 3: stats box (strategy vs buy & hold) =================
    ax_tbl.axis("off")
    is_s, is_h = _stats_block(in_sample)
    ho_s, ho_h = _stats_block(holdout)
    fu_s, fu_h = _stats_block(full)
    header = (
        "  "
        + " " * 26
        + f"{'tot ret':>11}{'Sharpe':>11}{'maxDD':>11}{'inMkt':>11}{'trades':>11}"
        + "  "
        + f"{'tot ret':>11}{'Sharpe':>11}{'maxDD':>11}{'inMkt':>11}{'trades':>11}"
    )
    group = "  " + " " * 26 + f"{'──────  STRATEGY  ──────':^55}  {'─────  BUY & HOLD  ─────':^55}"
    lines = [
        group,
        header,
        "  " + "─" * 138,
        _row("full history", fu_s, fu_h),
        _row("in-sample (exploratory)", is_s, is_h),
        _row("SEALED HOLDOUT  <- the test", ho_s, ho_h),
    ]
    ax_tbl.text(
        0.0,
        0.98,
        "\n".join(lines),
        family="monospace",
        fontsize=10,
        va="top",
        ha="left",
        transform=ax_tbl.transAxes,
    )
    ax_tbl.text(
        0.0,
        0.06,
        "In-sample = HMM fit on this span (exploratory, not OOS evidence).  "
        "Holdout = sealed before evaluation, model never saw it, evaluated once.  "
        "Returns compounded across the boundary; the ~21-day gap there is the "
        "holdout's causal vol-feature warmup (matches the sealed 231-day result).",
        fontsize=7.6,
        va="bottom",
        ha="left",
        color="#666666",
        transform=ax_tbl.transAxes,
    )
    # legend swatch for the two region tints
    handles = [
        Rectangle((0, 0), 1, 1, color="#9e9e9e", alpha=0.18),
        Rectangle((0, 0), 1, 1, color="#1f77b4", alpha=0.20),
    ]
    ax_tbl.legend(
        handles,
        ["in-sample / exploratory span", "sealed holdout span"],
        loc="lower right",
        fontsize=8,
        frameon=False,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    global _CLOSE
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    manifest = HoldoutManifest.from_json(_REPO_ROOT / "data" / "sample" / "holdout-manifest.json")
    _CLOSE = candles.close
    all_dates = candles.dates_iso()

    # One model, fit on the research partition ONLY (no look-ahead into holdout).
    research_close = load_research(candles.close, manifest)
    model = fit_regime_hmm(research_close, seed=42)

    # Causal strategy over the in-sample span and (revealed once) the holdout.
    r_research = causal_regime_returns(model, research_close)
    holdout_close = reveal_holdout(candles.close, manifest, ACKNOWLEDGEMENT)
    r_holdout = causal_regime_returns(model, holdout_close)

    in_sample = _segment("in-sample", all_dates, 0, r_research)
    holdout = _segment("holdout", all_dates, manifest.boundary_index, r_holdout)
    full = _full_segment(in_sample, holdout)

    _verify_holdout(holdout)

    out = _REPO_ROOT / "results" / "full-history-performance.png"
    render(in_sample, holdout, full, out)

    print("\n=== FULL-HISTORY PERFORMANCE (strategy vs buy & hold) ===")
    for seg in (full, in_sample, holdout):
        print(
            f"  {seg.label:<26} strat {seg.total_return * 100:+5.0f}% "
            f"(Sharpe {sharpe_ratio(seg.strat, _TRADING_DAYS):+.2f}, "
            f"maxDD {max_drawdown(seg.strat) * 100:.0f}%, "
            f"{seg.time_in_market * 100:.0f}% in mkt, {seg.n_trades} trades)   "
            f"hold {seg.hold_return * 100:+5.0f}% "
            f"(Sharpe {sharpe_ratio(seg.hold, _TRADING_DAYS):+.2f}, "
            f"maxDD {max_drawdown(seg.hold) * 100:.0f}%)"
        )
    print(f"\n  chart written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
