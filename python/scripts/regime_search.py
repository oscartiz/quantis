"""Honest multiple-testing evaluation of a regime-strategy *search*.

The repo ships Deflated-Sharpe and SPA/Reality-Check machinery to answer the
question every backtest search must face: *after trying N configurations and
keeping the best, is the winner's edge real, or is it what you'd expect from
searching that hard?* Until now those tools were only exercised on synthetic
tests. This script points them at the actual BTC regime strategy.

Design (no look-ahead, net of real funding):

* Search a grid of regime-model configs — volatility window x number of states —
  the genuine knobs a researcher would tune.
* Evaluate each **out-of-sample within the research partition**: fit on the first
  half, causally evaluate on the second half (net of HL funding). The sealed
  holdout is untouched here; it remains the separate one-shot test.
* All configs are aligned to a common date range, logged to a TrialLog, then:
    - Deflated Sharpe Ratio deflates the best config's Sharpe by the expected
      maximum across the whole search (Bailey & Lopez de Prado).
    - SPA / Reality Check bootstrap-test whether the best beats buy-and-hold
      given the entire set of trials.

The verdict is reported as-is. A low DSR / high SPA p-value would mean the search
found nothing the deflation can't explain — and that is a valid, honest result.

Run (from repo root):
    uv run --project python python python/scripts/regime_search.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from quantis.data.candles import load_candles
from quantis.data.funding import load_daily_funding
from quantis.data.holdout import HoldoutManifest
from quantis.evaluation.metrics import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)
from quantis.evaluation.multiple_testing import spa_test
from quantis.evaluation.regime_strategy import causal_regime_returns, fit_regime_hmm
from quantis.evaluation.trial_log import TrialLog, TrialRecord

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 365
_VOL_WINDOWS = [10, 15, 20, 30, 45, 60]
_N_STATES = [2, 3, 4]


@dataclass(frozen=True)
class Config:
    vol_window: int
    n_states: int

    @property
    def name(self) -> str:
        return f"hmm_k{self.n_states}_vol{self.vol_window}"


@dataclass(frozen=True)
class Evaluated:
    cfg: Config
    gidx: NDArray[np.int64]
    strat: Array
    hold: Array
    position: Array


def evaluate_config(
    cfg: Config, close: Array, funding: Array, *, train_end: int, slice_start: int, boundary: int
) -> Evaluated:
    """Fit on close[:train_end], causally evaluate OOS on (train_end, boundary)
    net of funding. Returns global-indexed OOS rows."""
    model = fit_regime_hmm(
        close[:train_end], seed=42, vol_window=cfg.vol_window, n_states=cfg.n_states
    )
    r = causal_regime_returns(
        model,
        close[slice_start:boundary],
        vol_window=cfg.vol_window,
        funding_daily=funding[slice_start:boundary],
        bull_rank=cfg.n_states - 1,
    )
    gidx = slice_start + r.candle_index
    oos = gidx >= train_end
    return Evaluated(cfg, gidx[oos], r.strat[oos], r.hold[oos], r.position[oos])


def main() -> int:
    candles = load_candles(_REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv")
    manifest = HoldoutManifest.from_json(_REPO_ROOT / "data" / "sample" / "holdout-manifest.json")
    funding = load_daily_funding(
        _REPO_ROOT / "data" / "sample" / "btc-funding.csv", candles.dates_iso()
    )
    close = candles.close

    boundary = manifest.boundary_index  # research = close[:boundary]; holdout untouched
    train_end = boundary // 2  # fit on first half of research, OOS on the second half
    slice_start = max(0, train_end - (max(_VOL_WINDOWS) + 5))

    configs = [Config(vw, ns) for vw in _VOL_WINDOWS for ns in _N_STATES]
    evals = [
        evaluate_config(
            c, close, funding, train_end=train_end, slice_start=slice_start, boundary=boundary
        )
        for c in configs
    ]

    # Align every config to the common OOS date range (warmup differs by vol_window).
    common = sorted(set.intersection(*[set(e.gidx.tolist()) for e in evals]))
    common_arr = np.array(common, dtype=np.int64)

    def aligned(e: Evaluated, field: str) -> Array:
        mask = np.isin(e.gidx, common_arr)
        return np.asarray(getattr(e, field), dtype=np.float64)[mask]

    hold = aligned(evals[0], "hold")  # identical across configs
    hold_sharpe = sharpe_ratio(hold, _TRADING_DAYS)

    # Fresh trial log each run (the search defines its own complete trial set).
    log_path = _REPO_ROOT / "results" / "regime-trial-log.jsonl"
    log_path.unlink(missing_ok=True)
    log = TrialLog(log_path)

    rows = []
    excess_cols = []
    strat_cols = []
    for e in evals:
        strat = aligned(e, "strat")
        pos = aligned(e, "position")
        log.append(TrialRecord(name=e.cfg.name, returns=[float(v) for v in strat]))
        excess_cols.append(strat - hold)
        strat_cols.append(strat)
        rows.append((e.cfg, sharpe_ratio(strat, _TRADING_DAYS), float(np.mean(pos)), strat))

    rows.sort(key=lambda r: r[1], reverse=True)
    n_obs = len(common_arr)
    print(
        f"search: {len(configs)} configs, OOS-within-research "
        f"({n_obs} aligned days, net of funding); buy & hold Sharpe {hold_sharpe:+.2f}\n"
    )
    print(f"  {'config':18}{'Sharpe(ann)':>12}{'in-mkt':>9}{'beats B&H':>11}")
    for cfg, sh, inmkt, _ in rows:
        beats = "yes" if sh > hold_sharpe else "no"
        print(f"  {cfg.name:18}{sh:>+12.2f}{inmkt * 100:>8.0f}%{beats:>11}")

    # --- multiple-testing correction over the whole search ---
    best_cfg, best_sharpe_ann, _, best_returns = rows[0]
    trial_sharpes = log.sharpes()  # per-period Sharpe of every trial
    psr = probabilistic_sharpe_ratio(best_returns, 0.0, 1.0)
    dsr = deflated_sharpe_ratio(best_returns, trial_sharpes, 1.0)
    spa_cash = spa_test(np.column_stack(strat_cols), seed=42)  # absolute edge (beats 0)
    spa_bh = spa_test(np.column_stack(excess_cols), seed=42)  # relative (beats buy & hold)

    n_beat = sum(1 for _, sh, _, _ in rows if sh > hold_sharpe)
    print("\n=== MULTIPLE-TESTING VERDICT (net of funding, OOS-within-research) ===")
    print(f"  configs beating buy & hold on Sharpe (raw): {n_beat}/{len(configs)}")
    print(f"  best config by Sharpe: {best_cfg.name}  (Sharpe ann {best_sharpe_ann:+.2f})")
    print("  -- absolute edge (is the best config's Sharpe real after the search?) --")
    print(f"  PSR (vs 0, uncorrected):          {psr:.3f}")
    print(f"  Deflated Sharpe ({len(configs)} configs):  {dsr:.3f}   (deflates PSR for the search)")
    print(f"  SPA p-value (best beats cash):    {spa_cash.spa_pvalue:.3f}")
    print("  -- relative (does the best beat buy & hold on this bull-dominated span?) --")
    print(f"  SPA p-value (best beats B&H):     {spa_bh.spa_pvalue:.3f}")
    print(f"  Reality Check p-value (conserv.): {spa_bh.reality_check_pvalue:.3f}")
    survives = dsr > 0.95 and spa_cash.spa_pvalue < 0.05
    verdict = "absolute edge survives the search" if survives else "no edge survives the correction"
    print(f"  --> {verdict}")

    artifact = {
        "n_configs": len(configs),
        "n_obs": n_obs,
        "eval": "OOS-within-research (fit first half, causal second half, net of funding)",
        "buy_hold_sharpe_ann": hold_sharpe,
        "best_config": best_cfg.name,
        "best_sharpe_ann": best_sharpe_ann,
        "psr_uncorrected": psr,
        "deflated_sharpe": dsr,
        "spa_pvalue_vs_cash": spa_cash.spa_pvalue,
        "spa_pvalue_vs_buy_hold": spa_bh.spa_pvalue,
        "reality_check_pvalue_vs_buy_hold": spa_bh.reality_check_pvalue,
        "configs_beating_buy_hold": n_beat,
    }
    out = _REPO_ROOT / "results" / "regime-search.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"\n  artifacts: {out}  and  {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
