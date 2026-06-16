"""Minimal daily regime signal emitter (paper observation only).

Turns the *research* regime strategy into a forward-running, once-a-day
LONG/FLAT signal — so it can be run forward and accumulate a *real*
out-of-sample track record (the thing that turns the N=1 holdout into evidence).

It does exactly two things, and deliberately nothing more:

    freeze   fit the 3-state HMM once on all available history and persist the
             frozen parameters (no silent refits — the live signal is auditable).
    signal   load the frozen model, score the latest daily bar with the *causal*
             filtered posterior (no look-ahead), print LONG/FLAT, and append the
             decision to a CSV track-record log.

It reuses the repo's exact strategy definition (`quantis.evaluation.regime_strategy`),
so the emitted rule is byte-for-byte the one that was backtested:
features = (log_return lag1, realized_vol 20); long iff the filtered regime is
bull (rank 2), flat otherwise; decided at today's close, held into the next bar.

SAFETY: this emits a *signal*, never an order. It does not touch any exchange.
Mainnet is blocked at the engine config layer regardless; wire `signal` to the
paper/testnet gateway only after you trust the forward log.

Run (from repo root):
    uv run --project python python python/scripts/daily_signal.py freeze
    uv run --project python python python/scripts/daily_signal.py signal
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quantis.data.candles import load_candles
from quantis.evaluation.regime_strategy import (
    _BULL,
    DEFAULT_VOL_WINDOW,
    fit_regime_hmm,
    ordered_regimes,
    regime_features,
)
from quantis.models.hmm import GaussianHMM, HmmParams

Array = NDArray[np.float64]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CSV = _REPO_ROOT / "data" / "sample" / "btc-1d-candles.csv"
_FROZEN = _REPO_ROOT / "results" / "frozen-regime-model.json"
_LOG = _REPO_ROOT / "results" / "daily-signal-log.csv"
_REGIME_NAMES = {0: "bear", 1: "chop", 2: "bull"}


# --- persistence of the frozen model -----------------------------------------


def _close_sha256(close: Array) -> str:
    return hashlib.sha256(np.ascontiguousarray(close, dtype="<f8").tobytes()).hexdigest()


def freeze(csv_path: Path, out_path: Path) -> None:
    """Fit the regime HMM on ALL available history and persist its parameters."""
    candles = load_candles(csv_path)
    model = fit_regime_hmm(candles.close, seed=42, vol_window=DEFAULT_VOL_WINDOW)
    p = model.params_
    assert p is not None
    artifact = {
        "trained_through": candles.dates_iso()[-1],
        "n_train_bars": len(candles),
        "train_close_sha256": _close_sha256(candles.close),
        "seed": 42,
        "n_states": model.n_states,
        "vol_window": DEFAULT_VOL_WINDOW,
        "startprob": p.startprob.tolist(),
        "transmat": p.transmat.tolist(),
        "means": p.means.tolist(),
        "variances": p.variances.tolist(),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(
        f"froze model trained through {artifact['trained_through']} "
        f"({artifact['n_train_bars']} bars) -> {out_path}"
    )


def _load_frozen(path: Path) -> tuple[GaussianHMM, dict[str, Any]]:
    meta = json.loads(path.read_text(encoding="utf-8"))
    params = HmmParams(
        startprob=np.array(meta["startprob"], dtype=np.float64),
        transmat=np.array(meta["transmat"], dtype=np.float64),
        means=np.array(meta["means"], dtype=np.float64),
        variances=np.array(meta["variances"], dtype=np.float64),
    )
    model = GaussianHMM(n_states=int(meta["n_states"]))
    model.params_ = params  # load fitted params directly; no refit, no look-ahead
    return model, meta


# --- scoring the latest bar ---------------------------------------------------


def score_latest(model: GaussianHMM, close: Array, vol_window: int) -> tuple[int, int]:
    """Return (row_index_of_latest_warmed_bar, ordered_regime) using the causal
    filtered posterior — the regime as of the most recent close, no future data."""
    fm = regime_features(close, vol_window)
    valid = np.flatnonzero(fm.valid)
    filtered = ordered_regimes(model, model.filter_proba(fm.values[valid]))
    return int(valid[-1]), int(filtered[-1])


def emit(csv_path: Path, frozen_path: Path, log_path: Path, *, notify: bool) -> None:
    if not frozen_path.exists():
        raise SystemExit(f"no frozen model at {frozen_path}; run `freeze` first.")
    model, meta = _load_frozen(frozen_path)
    candles = load_candles(csv_path)

    row, regime = score_latest(model, candles.close, int(meta["vol_window"]))
    asof_date = candles.dates_iso()[row]
    asof_close = float(candles.close[row])
    target = "LONG" if regime == _BULL else "FLAT"

    prev_date, prev = _last_logged(log_path)
    action = (
        "ENTER"
        if prev != "LONG" and target == "LONG"
        else "EXIT"
        if prev == "LONG" and target == "FLAT"
        else "HOLD"
    )

    already = prev_date == asof_date  # idempotent: one row per bar, safe to re-run
    if not already:
        _append_log(
            log_path,
            asof_date,
            asof_close,
            _REGIME_NAMES[regime],
            target,
            action,
            str(meta["trained_through"]),
        )

    msg = (
        f"[{dt.datetime.now(dt.UTC):%Y-%m-%dT%H:%MZ}] as of close {asof_date} "
        f"BTC ${asof_close:,.0f}: regime={_REGIME_NAMES[regime]} -> target {target} "
        f"({action}, effective next daily bar). model trained through {meta['trained_through']}."
    )
    print(msg + ("  [already logged for this bar]" if already else ""))
    if asof_date != meta["trained_through"]:
        print(
            f"  note: scoring {asof_date} on a model frozen at {meta['trained_through']} "
            "(frozen params, causal). Re-`freeze` periodically — but treat re-fitting as a "
            "research action and do not retune hyperparameters on the forward log."
        )
    if notify:
        _notify(msg)


# --- track-record log ---------------------------------------------------------


def _last_logged(log_path: Path) -> tuple[str, str]:
    """(date, target) of the last logged bar; ('', 'FLAT') if the log is empty."""
    if not log_path.exists():
        return "", "FLAT"  # start flat
    rows = log_path.read_text(encoding="utf-8").splitlines()
    if len(rows) <= 1:
        return "", "FLAT"
    cells = rows[-1].split(",")
    return cells[0], cells[3]


def _append_log(
    log_path: Path,
    date: str,
    close: float,
    regime: str,
    target: str,
    action: str,
    trained_through: str,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["date", "close", "regime", "target", "action", "model_trained_through"])
        w.writerow([date, f"{close:.2f}", regime, target, action, trained_through])


# --- optional notification seam (stdlib only) --------------------------------


def _notify(message: str) -> None:
    """POST the message to a webhook if QUANTIS_SIGNAL_WEBHOOK is set; else no-op.
    Wire this to Slack/Discord/email as you like — it stays a print otherwise."""
    import os
    import urllib.request

    url = os.environ.get("QUANTIS_SIGNAL_WEBHOOK")
    if not url:
        return
    data = json.dumps({"text": message}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Daily regime signal emitter (paper observation only)."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("freeze", help="fit the HMM on all history and persist frozen params")
    sp = sub.add_parser("signal", help="score the latest bar and emit LONG/FLAT")
    sp.add_argument("--notify", action="store_true", help="POST to QUANTIS_SIGNAL_WEBHOOK if set")
    args = ap.parse_args()

    if args.cmd == "freeze":
        freeze(_CSV, _FROZEN)
    else:
        emit(_CSV, _FROZEN, _LOG, notify=args.notify)
    return 0


if __name__ == "__main__":
    sys.exit(main())
