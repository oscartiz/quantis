"""One consolidated research report — every study and one *global* correction.

Each study (`regime_search`, `ensemble_eval`, `sizing_eval`, `short_eval`)
already deflates and PBO-tests *its own* search. But the project has now run all
four, and the honest deflation benchmark for "did we find anything?" is the
**union** of every configuration ever tried, not each search in isolation —
otherwise the cross-experiment selection (run four searches, report the best of
all four) goes uncorrected. Most backtests quietly ignore this; this report makes
it explicit.

It reads the four studies' trial logs and JSON artifacts (produced by running
those scripts — `make research` runs them first), then:

* tabulates each study's best config, Deflated Sharpe, SPA p-values and PBO;
* pools **every** logged trial and computes a single global Deflated Sharpe: the
  best trial across all studies, deflated by the expected maximum Sharpe over the
  entire union of trials. The more you search across experiments, the higher that
  bar — so a winner that looked fine inside one study can still fail globally.

Output: a self-contained ``results/research-report.html`` and a JSON artifact.

Run (from repo root):
    make research        # runs the four studies, then this report
    uv run --project python python python/scripts/research_report.py   # report only
"""

from __future__ import annotations

import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from quantis.evaluation.metrics import expected_max_sharpe, probabilistic_sharpe_ratio
from quantis.evaluation.trial_log import TrialLog

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESULTS = _REPO_ROOT / "results"


@dataclass(frozen=True)
class Study:
    key: str
    title: str
    artifact: str
    trial_log: str
    best_key: str
    rel_spa_key: str
    rel_label: str


_STUDIES = [
    Study(
        "regime",
        "Regime-model search (HMM vol_window x n_states)",
        "regime-search.json",
        "regime-trial-log.jsonl",
        "best_config",
        "spa_pvalue_vs_buy_hold",
        "vs buy &amp; hold",
    ),
    Study(
        "ensemble",
        "HMM + BOCPD risk-off overlay",
        "ensemble-eval.json",
        "ensemble-trial-log.jsonl",
        "best_overlay",
        "spa_pvalue_vs_hmm",
        "vs plain HMM",
    ),
    Study(
        "sizing",
        "Position sizing (vol-target + Kelly)",
        "sizing-eval.json",
        "sizing-trial-log.jsonl",
        "best_sizing",
        "spa_pvalue_vs_binary",
        "vs binary book",
    ),
    Study(
        "short",
        "Long/short (short the bear regime)",
        "short-eval.json",
        "short-trial-log.jsonl",
        "best_long_short",
        "spa_pvalue_vs_long_flat",
        "vs long/flat",
    ),
]


@dataclass(frozen=True)
class StudyRow:
    title: str
    best: str
    n_trials: int
    dsr: float
    spa_cash: float
    spa_rel: float
    rel_label: str
    pbo: float


def _load_present() -> list[tuple[Study, dict]]:
    present = []
    for s in _STUDIES:
        path = _RESULTS / s.artifact
        if path.exists():
            present.append((s, json.loads(path.read_text(encoding="utf-8"))))
    return present


def _study_rows(present: list[tuple[Study, dict]]) -> list[StudyRow]:
    rows = []
    for s, art in present:
        n_trials = len(TrialLog(_RESULTS / s.trial_log).load())
        rows.append(
            StudyRow(
                title=s.title,
                best=str(art[s.best_key]),
                n_trials=n_trials,
                dsr=float(art["deflated_sharpe"]),
                spa_cash=float(art["spa_pvalue_vs_cash"]),
                spa_rel=float(art[s.rel_spa_key]),
                rel_label=s.rel_label,
                pbo=float(art["pbo"]),
            )
        )
    return rows


def _global_correction(present: list[tuple[Study, dict]]) -> dict:
    """Pool every trial across studies; deflate the global best by the expected
    maximum Sharpe over the entire union."""
    records = []
    for s, _ in present:
        records.extend(TrialLog(_RESULTS / s.trial_log).load())
    if not records:
        raise ValueError("no trials found; run the studies first (make research)")
    sharpes = np.array([r.sharpe() for r in records], dtype=np.float64)
    best = max(records, key=lambda r: r.sharpe())
    benchmark = expected_max_sharpe(sharpes)
    union_dsr = probabilistic_sharpe_ratio(
        np.asarray(best.returns, dtype=np.float64), benchmark, 1.0
    )
    return {
        "n_trials_total": len(records),
        "best_trial": best.name,
        "best_trial_sharpe_per_period": float(best.sharpe()),
        "expected_max_sharpe_benchmark": float(benchmark),
        "global_deflated_sharpe": float(union_dsr),
        "edge_survives_global": bool(union_dsr > 0.95),
    }


def _render_html(rows: list[StudyRow], g: dict) -> str:
    def pbo_cell(p: float) -> str:
        cls = "bad" if p > 0.5 else "ok"
        return f'<td class="{cls}">{p:.3f}</td>'

    def dsr_cell(d: float) -> str:
        cls = "ok" if d > 0.95 else "bad"
        return f'<td class="{cls}">{d:.3f}</td>'

    study_rows = "\n".join(
        f"<tr><td>{html.escape(r.title)}</td><td>{html.escape(r.best)}</td>"
        f"<td>{r.n_trials}</td>{dsr_cell(r.dsr)}<td>{r.spa_cash:.3f}</td>"
        f"<td>{r.spa_rel:.3f} <span class='muted'>({r.rel_label})</span></td>{pbo_cell(r.pbo)}</tr>"
        for r in rows
    )
    g_dsr_cls = "ok" if g["edge_survives_global"] else "bad"
    verdict = (
        "an edge survives the GLOBAL correction"
        if g["edge_survives_global"]
        else "NO edge survives once every study's trials are corrected together"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Quantis — consolidated research report</title>
<style>
 body {{ font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem auto;
        max-width: 60rem; color: #1a1a1a; }}
 h1 {{ font-size: 1.5rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 table {{ border-collapse: collapse; width: 100%; margin: 1rem 0;
         font-variant-numeric: tabular-nums; }}
 th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: right; }}
 th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
 th {{ background: #f4f4f4; }} .muted {{ color: #888; font-size: 0.85em; }}
 .ok {{ color: #0a7d28; font-weight: 600; }} .bad {{ color: #b00020; font-weight: 600; }}
 .panel {{ border: 2px solid #1a1a1a; border-radius: 8px; padding: 1rem 1.25rem;
          margin: 1.5rem 0; }}
 .verdict {{ font-size: 1.05rem; font-weight: 600; }}
 footer {{ color: #888; font-size: 0.85em; margin-top: 2rem; }}
</style></head><body>
<h1>Quantis — consolidated research report</h1>
<p>Every research study and its honest correction, plus a single <b>global</b>
correction that deflates the best configuration found <i>anywhere</i> by the
expected maximum Sharpe over <b>all {g["n_trials_total"]}</b> trials across all
studies. DSR &gt; 0.95 and PBO &lt; 0.5 are the survival thresholds; values that
fail are red.</p>

<h2>Per-study corrections</h2>
<table>
<tr><th>Study</th><th>Best config</th><th>Trials</th><th>Deflated Sharpe</th>
<th>SPA vs cash</th><th>SPA (relative)</th><th>PBO</th></tr>
{study_rows}
</table>

<div class="panel">
<h2 style="margin-top:0">Global correction (union of all {g["n_trials_total"]} trials)</h2>
<p>Best trial anywhere: <b>{html.escape(g["best_trial"])}</b>
(per-period Sharpe {g["best_trial_sharpe_per_period"]:.3f}).
Expected-max-Sharpe benchmark over the union: {g["expected_max_sharpe_benchmark"]:.3f}.</p>
<p class="verdict">Global Deflated Sharpe:
<span class="{g_dsr_cls}">{g["global_deflated_sharpe"]:.3f}</span> &nbsp;→&nbsp; {verdict}.</p>
</div>

<footer>Reproduce: <code>make research</code>. Net of real Hyperliquid funding,
OOS-within-research, holdout sealed. This report asserts nothing the numbers do
not — see docs/statistical-honesty.md and ADR-009.</footer>
</body></html>
"""


def main() -> int:
    present = _load_present()
    if not present:
        print("no study artifacts found in results/; run the studies first (make research)")
        return 1
    rows = _study_rows(present)
    g = _global_correction(present)

    print(f"consolidated report: {len(rows)} studies, {g['n_trials_total']} total trials\n")
    print(f"  {'study':42}{'trials':>7}{'DSR':>7}{'PBO':>7}")
    for r in rows:
        print(f"  {r.title[:42]:42}{r.n_trials:>7}{r.dsr:>7.3f}{r.pbo:>7.3f}")
    print("\n=== GLOBAL CORRECTION (union of all trials) ===")
    print(f"  total trials across all studies: {g['n_trials_total']}")
    print(
        f"  best trial anywhere: {g['best_trial']} "
        f"(Sharpe/period {g['best_trial_sharpe_per_period']:.3f})"
    )
    print(f"  expected-max-Sharpe benchmark:   {g['expected_max_sharpe_benchmark']:.3f}")
    print(f"  GLOBAL Deflated Sharpe:          {g['global_deflated_sharpe']:.3f}")
    survived = (
        "edge survives global correction"
        if g["edge_survives_global"]
        else ("no edge survives the global correction")
    )
    print(f"  --> {survived}")

    html_out = _RESULTS / "research-report.html"
    html_out.write_text(_render_html(rows, g), encoding="utf-8")
    json_out = _RESULTS / "research-report.json"
    json_out.write_text(
        json.dumps({"studies": [r.__dict__ for r in rows], "global_correction": g}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"\n  artifacts: {html_out}  and  {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
