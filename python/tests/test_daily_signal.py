"""Forward track record: realized returns reconstructed from a signal log.

The reconstruction must reuse the repo's exact conventions — the position
decided on a bar is held into the NEXT bar (so the last logged row, with no next
close, is dropped), returns are close-to-close logs, and a 5 bps cost is charged
on every position change starting from flat.
"""

import importlib.util
import pathlib
import sys
from math import log

import numpy as np

# daily_signal lives in scripts/, not the quantis package, so load it directly.
# Register it in sys.modules before exec so its frozen @dataclass can resolve its
# own __module__ (dataclasses look the defining module up by name).
_P = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "daily_signal.py"
_SPEC = importlib.util.spec_from_file_location("daily_signal", _P)
ds = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
sys.modules["daily_signal"] = ds
_SPEC.loader.exec_module(ds)  # type: ignore[union-attr]

_COST = 5.0 / 10_000.0  # DEFAULT_COST_BPS == 5.0


def _row(date: str, close: float, target: str) -> dict[str, str]:
    return {"date": date, "close": str(close), "target": target}


def test_fewer_than_two_rows_returns_none() -> None:
    assert ds.compute_track_record([]) is None
    assert ds.compute_track_record([_row("2024-01-01", 100.0, "LONG")]) is None


def test_hand_computed_three_bar_case() -> None:
    rows = [
        _row("2024-01-01", 100.0, "LONG"),
        _row("2024-01-02", 110.0, "LONG"),
        _row("2024-01-03", 99.0, "FLAT"),  # last row is dropped (no next close)
    ]
    tr = ds.compute_track_record(rows)
    assert tr is not None

    # Two LONG positions held into the two realized returns.
    assert np.array_equal(tr.position, [1.0, 1.0])
    assert len(tr.strat) == 2
    assert tr.dates == ["2024-01-01", "2024-01-02"]

    # Buy-and-hold is the raw close-to-close log return over each held bar.
    assert np.allclose(tr.hold, [log(110.0 / 100.0), log(99.0 / 110.0)])
    # Strategy nets a cost only on the first bar (start flat -> LONG entry);
    # the second bar holds LONG so it carries no cost.
    assert np.allclose(tr.strat, [log(110.0 / 100.0) - _COST, log(99.0 / 110.0)])

    assert tr.n_trades == 1  # a single FLAT -> LONG entry
    assert tr.gaps == 0  # consecutive calendar days

    # strat_return / hold_return compound the held log returns.
    assert tr.strat_return == np.exp(np.sum(tr.strat)) - 1.0
    assert tr.hold_return == np.exp(np.sum(tr.hold)) - 1.0


def test_n_trades_counts_flat_to_long_entries() -> None:
    # Targets cycle LONG/FLAT; the LAST row is dropped, so only entries among the
    # held positions count. Held positions are [LONG, FLAT, LONG, FLAT] -> two
    # FLAT->LONG entries (the first from the implicit start-flat, the second
    # mid-sequence). The trailing LONG row is never held.
    rows = [
        _row("2024-01-01", 100.0, "LONG"),
        _row("2024-01-02", 100.0, "FLAT"),
        _row("2024-01-03", 100.0, "LONG"),
        _row("2024-01-04", 100.0, "FLAT"),
        _row("2024-01-05", 100.0, "LONG"),  # dropped: no next close
    ]
    tr = ds.compute_track_record(rows)
    assert tr is not None
    assert np.array_equal(tr.position, [1.0, 0.0, 1.0, 0.0])
    assert tr.n_trades == 2


def test_non_consecutive_date_step_increments_gaps() -> None:
    # Jan-02 -> Jan-04 skips a calendar day: one gap. The other steps are
    # consecutive (and the final row is dropped, so its date is not paired).
    rows = [
        _row("2024-01-01", 100.0, "LONG"),
        _row("2024-01-02", 100.0, "LONG"),
        _row("2024-01-04", 100.0, "LONG"),
        _row("2024-01-05", 100.0, "FLAT"),
    ]
    tr = ds.compute_track_record(rows)
    assert tr is not None
    assert tr.gaps == 1
