# ADR-005: Risk framework

- Status: accepted
- Date: 2026-06-13

## Context

A strategy that is right on average can still be ruined by a single oversized
position or an unbroken losing streak. The risk layer exists to make ruin
*structurally hard* rather than relying on the strategy to behave. It must
provide position sizing, per-trade and portfolio limits, and a kill switch,
and it must be able to veto any order before it reaches a venue.

## Decision

`crates/risk` is split into two parts with different trust levels:

### Sizing (advisory, may use floating point)

- **Volatility targeting** — size the position so its expected volatility
  matches a target: `notional = equity × target_vol / realized_vol`, capped at
  `max_leverage × equity`. A collapsing volatility estimate cannot demand an
  unbounded position because the leverage cap binds.
- **Capped fractional Kelly** — `f = clamp(fraction × edge / variance, ±cap)`.
  Full Kelly is famously too aggressive under parameter uncertainty, so the
  default posture is a *fraction* of Kelly with a hard cap; the sign is
  preserved so a negative edge proposes a short.

Sizing is *advisory*: it proposes a quantity from f64 research estimates that
can be wrong. Being wrong here is not dangerous, because of the second part.

### The gate (authority, integer-only)

`RiskGate` is the last line of defence and is deliberately simple and integer:

- **Pre-trade veto** — `check_order` returns `Allow` or `Veto(reason)` for any
  proposed order. A risk-*increasing* order must clear the position cap and the
  notional cap; breaches are vetoed with a typed reason.
- **De-risking always passes** — an order that shrinks the absolute position is
  always allowed, even with the kill switch tripped. You can always flatten.
- **Portfolio drawdown limit + kill switch** — `on_equity` tracks peak equity;
  when drawdown from the peak reaches the configured fraction, the kill switch
  *latches*. While tripped, every risk-increasing order is vetoed. It does not
  auto-reset on recovery — clearing it is an explicit operator action, because
  a drawdown breach warrants investigation, not an automatic resumption.

## Why split sizing from the gate

The two have different failure modes and so different designs:

- Sizing's job is to be *approximately optimal*; it can use floating-point
  estimates and be occasionally wrong.
- The gate's job is to be *never catastrophically wrong*; it uses only integer
  comparisons against hard caps, so its correctness is simple to state and to
  test exhaustively.

Because the gate bounds position, notional, and drawdown independently of
sizing, no sizing bug can produce an oversized position. That separation is the
whole point.

## Verification

The safety properties are checked with **property-based tests** (`proptest`),
not just examples, because the gate must hold for *every* order:

- an allowed risk-increasing order is always within both caps;
- a de-risking order is always allowed, even when killed;
- a killed gate blocks every risk-increasing order.

Each property is explored over hundreds of randomized cases.

## Alternatives considered

- **One combined "smart" sizer with limits baked in.** Rejected: it conflates
  the advisory and authoritative roles, making the hard guarantee harder to
  state and test. The whole value is that the gate is dumb and certain.
- **Auto-resetting kill switch (resume when equity recovers).** Rejected: a
  drawdown breach is exactly when a human should look before risking more;
  silent auto-resume defeats the purpose.
- **Full Kelly default.** Rejected as a default: too aggressive under real
  parameter uncertainty; fractional-with-cap is the safe default, full Kelly
  available by setting `fraction = 1`.

## Consequences

- Strategies propose; the gate disposes. Every order in backtest, paper, and
  testnet flows through the same `check_order`, so the safety guarantees are
  identical across all three (Phase 4 wires it into execution).
- The gate's caps are config, not code, and fail closed on invalid values.
- A reducing-order carve-out means the system can always de-risk — important
  for the chaos test (Phase 4), where flattening must work even mid-incident.
