# ADR-004: Backtester fill model

- Status: accepted
- Date: 2026-06-13

## Context

A backtest is only as trustworthy as its fill model. The v0 engine (Phase 1)
filled market orders *instantly* against the snapshot that triggered them, with
taker fees and book-walk slippage but no latency and no funding. That is
optimistic in two ways that matter for a perpetual-futures strategy, and this
ADR records the v1 model that closes them — plus an honest statement of what it
still cannot capture at snapshot resolution.

## Decision

The fill model (`crates/backtest`, the single source of truth shared with the
Phase 4 paper gateway) models, as of v1:

1. **Book-walk slippage** (from v0). A market order consumes visible levels in
   price order; depth it cannot fill is *reported* as `unfilled_qty`, never
   invented. Integer-exact.
2. **Maker/taker fees** (from v0). Parts-per-million of notional, from config.
   The taker path is exercised by the shipped strategy; the maker path is wired
   for the limit-order extension below.
3. **Execution latency / adverse selection** (v1). An order submitted while
   processing a snapshot is *queued*, not filled there. It carries an arrival
   time `submit_ts + latency_ms` and fills against the first later snapshot
   whose timestamp has reached arrival. Because arrived orders are executed
   before newly-submitted ones are enqueued, there is **always** at least a
   one-snapshot execution delay — you can never fill at the price that produced
   your signal.
4. **Funding** (v1). A position open across a funding-interval boundary accrues
   `signed_notional × funding_rate_ppm`, signed so that positive rates cost
   longs and pay shorts. Integer-exact.

All four are pure-integer and seed-free, so the deterministic results artifact
stays bit-reproducible; this change deliberately bumped the committed golden
hash (`tests/smoke/expected_hash.txt`), recorded in the same commit.

## The honest limitation: latency at snapshot resolution

Hyperliquid's `l2Book` feed is a *snapshot* feed at roughly two per second. Our
latency model can therefore only resolve delays in units of the inter-snapshot
gap (~500 ms). Concretely, measured on the bundled sample:

| latency | net PnL on sample | effect |
|---|---|---|
| 0 ms | −2.824322 | one-snapshot delay (structural) |
| 600 ms | −2.824322 | no change |
| 5 000 ms | −2.824322 | no change |
| 30 000 ms | −2.174408 | execution clearly moves |

Sub-snapshot latency (anything below ~500 ms) is **below the data's
resolution** and is a no-op beyond the structural one-snapshot delay. This is
not a bug — it is the honest ceiling of what snapshot data can support, and it
is asserted by a test (`latency_resolution_limit_is_honest`). Modelling true
microsecond latency and queue position needs tick-by-tick L3 data, which is
future work (see `docs/scaling.md`). Until then, **backtest latency cost is a
lower bound**, and `docs/losing-money.md` treats latency as a sensitivity axis
rather than a settled number.

## Resting limit orders and queue position (now built and exercised)

The maker path is implemented and tested end to end (`FillEngine::match_resting`,
the engine's trade-driven resting-order loop, and the `PassiveMaker` strategy):

- A resting limit order joins the **back** of the queue at its level. It fills
  only after all size that was ahead of it at submission has traded through (the
  *conservative* / pessimistic queue assumption: you are last in line), then
  fills at its resting price (no improvement) with the **maker** fee. With
  snapshot data we cannot observe true queue position, so the pessimistic
  assumption is the honest default — it never *over*-credits maker fills.
- A resting buy is filled only by sell-aggressor trades at or below its price
  (and vice-versa); the queue ahead is seeded from the visible size at the level
  when the order arrives.

This is verified two ways: a unit test that the order waits for the queue to
clear before filling at the maker fee, and an engine test that runs the maker
strategy with the **taker fee non-zero but the maker fee zero** and asserts the
realized fees are exactly zero — proving every fill came through the resting
(maker) path, not the taker path. The market path and its golden hash are
unchanged by this addition.

Still simplified (honest scope): the maker strategy holds one resting order at a
time and does not model cancellation/replacement latency or partial-fill queue
re-estimation; those, and true L3 queue position, need tick data (`scaling.md`).

## Alternatives considered

- **Fill at the signal snapshot (v0).** Rejected: it lets the strategy trade at
  a price it could not actually have reached, a subtle look-ahead in execution.
- **Optimistic queue (fill as soon as price is touched).** Rejected: it
  systematically over-credits maker fills and flatters maker strategies — the
  opposite of the honesty posture.
- **Random latency draws.** Rejected for the deterministic core: it would break
  bit-reproducibility. Latency is a fixed config input; its *uncertainty* is
  explored as a sensitivity sweep in the evaluation layer, not injected as RNG
  into the single source of truth.

## Consequences

- Backtest and paper trading share this exact model, so their gap (Phase 4)
  comes from data and real network latency, not divergent fill logic.
- Reported costs are honest lower bounds at snapshot resolution; the limitation
  is documented, tested, and surfaced as a sensitivity rather than hidden.
- The golden hash moves whenever this model changes — by design, so no fill
  logic change can slip through unreviewed.
