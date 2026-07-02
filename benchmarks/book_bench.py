"""Pure-Python order-book and event-loop benchmark, mirroring the Rust hot
path as faithfully as CPython allows. Feeds the comparison table in ADR-002.

Honest framing: this is idiomatic *pure* CPython (lists, bisect, deque).
NumPy is deliberately absent because an event-driven loop with path-dependent
state (position, rolling windows, conditional orders) does not vectorize
cleanly; the realistic Python alternative for this workload is exactly this
kind of code. Run: `uv run --project python python benchmarks/book_bench.py`
"""

import time
from bisect import insort
from collections import deque

DEPTH = 20
TICK = 50_000_000  # $0.5 in 1e8 fixed point
SNAPSHOTS = 1_000
LEVEL_UPDATES = 10_000
LOOP_EVENTS = 100_000
REPEATS = 5


def make_snapshots() -> list[tuple[list[tuple[int, int]], list[tuple[int, int]]]]:
    mid = 100_000 * 100_000_000
    out = []
    for i in range(SNAPSHOTS):
        mid += ((i * 31) % 21 - 10) * TICK
        bids = []
        asks = []
        for lvl in range(DEPTH):
            qty = 10_000_000 + (i * 7 + lvl * 13) % 400_000_000
            bids.append((mid - TICK // 2 - lvl * TICK, qty))
            asks.append((mid + TICK // 2 + lvl * TICK, qty))
        out.append((bids, asks))
    return out


def apply_snapshot_workload() -> float:
    """Rebuild ladders per snapshot, mirroring OrderBook::apply_snapshot
    (drop bad qty, verify sortedness, crossed check)."""
    snaps = make_snapshots()
    best_ns = float("inf")
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        bids: list[tuple[int, int]] = []
        asks: list[tuple[int, int]] = []
        crossed = 0
        for snap_bids, snap_asks in snaps:
            bids = [lvl for lvl in snap_bids if lvl[1] > 0]
            asks = [lvl for lvl in snap_asks if lvl[1] > 0]
            if any(bids[i][0] <= bids[i + 1][0] for i in range(len(bids) - 1)):
                bids.sort(key=lambda lvl: -lvl[0])
            if any(asks[i][0] >= asks[i + 1][0] for i in range(len(asks) - 1)):
                asks.sort(key=lambda lvl: lvl[0])
            if bids and asks and bids[0][0] >= asks[0][0]:
                crossed += 1
        elapsed = time.perf_counter() - t0
        best_ns = min(best_ns, elapsed * 1e9 / SNAPSHOTS)
    assert crossed == 0
    return best_ns


def apply_level_workload() -> float:
    """Sorted-list level updates via bisect, mirroring apply_level."""
    mid = 100_000 * 100_000_000
    updates = []
    for i in range(LEVEL_UPDATES):
        is_bid = i % 2 == 0
        offset = TICK // 2 + ((i * 17) % 40) * TICK
        px = mid - offset if is_bid else mid + offset
        qty = 0 if i % 5 == 0 else 10_000_000 + (i * 13) % 200_000_000
        updates.append((is_bid, px, qty))

    best_ns = float("inf")
    for _ in range(REPEATS):
        bids: list[int] = []
        asks: list[int] = []
        bid_qty: dict[int, int] = {}
        ask_qty: dict[int, int] = {}
        t0 = time.perf_counter()
        for is_bid, px, qty in updates:
            keys, qtys = (bids, bid_qty) if is_bid else (asks, ask_qty)
            if qty <= 0:
                if px in qtys:
                    del qtys[px]
                    keys.remove(px)
            elif px in qtys:
                qtys[px] = qty
            else:
                insort(keys, px)
                qtys[px] = qty
        elapsed = time.perf_counter() - t0
        best_ns = min(best_ns, elapsed * 1e9 / LEVEL_UPDATES)
    return best_ns


def event_loop_workload() -> float:
    """Mini backtest loop: rolling SMA sums + crossover + position flip,
    mirroring SmaCross + accounting on precomputed mids."""
    mids = []
    mid = 100_000 * 100_000_000
    for i in range(LOOP_EVENTS):
        mid += ((i * 31) % 21 - 10) * TICK
        mids.append(mid)

    fast_n, slow_n = 120, 600
    best_ns = float("inf")
    for _ in range(REPEATS):
        window: deque[int] = deque()
        fast_sum = 0
        slow_sum = 0
        last_above = None
        position = 0
        cash = 100_000 * 100_000_000
        fills = 0
        t0 = time.perf_counter()
        for m in mids:
            window.append(m)
            fast_sum += m
            slow_sum += m
            if len(window) > fast_n:
                fast_sum -= window[-1 - fast_n]
            if len(window) > slow_n:
                slow_sum -= window.popleft()
            if len(window) < slow_n:
                continue
            fast_above = fast_sum * slow_n > slow_sum * fast_n
            if fast_above == last_above:
                continue
            last_above = fast_above
            target = 1_000_000 if fast_above else -1_000_000
            delta = target - position
            if delta:
                position += delta
                cash -= delta * (m // 100_000_000)
                fills += 1
        elapsed = time.perf_counter() - t0
        best_ns = min(best_ns, elapsed * 1e9 / LOOP_EVENTS)
    assert fills > 0
    return best_ns


def main() -> None:
    print(f"pure CPython, best of {REPEATS} runs:")
    print(f"  apply_snapshot (20 levels/side): {apply_snapshot_workload():8.0f} ns/op")
    print(f"  apply_level (sorted list):       {apply_level_workload():8.0f} ns/op")
    print(f"  event loop (SMA 120/600):        {event_loop_workload():8.0f} ns/op")


if __name__ == "__main__":
    main()
