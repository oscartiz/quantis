# Backtest vs. paper: the gap, measured and attributed

A backtest you cannot reproduce in paper trading is a fiction. This document
compares the two on the bundled sample and attributes the difference — because
the *size and cause* of the gap is itself a result, and hiding it would defeat
the purpose of sharing one fill engine across both.

## The measurement

Same config, same data (`data/sample/btc-sample.qnts`), same SMA strategy:

```sh
quantis backtest --config config/engine.example.toml
quantis trade    --config config/engine.example.toml --replay data/sample/btc-sample.qnts
```

| | backtest | paper (replay) |
|---|---|---|
| fills | 4 | 4 |
| fees | 2.009322 | 2.009322 |
| end position | −0.01 | −0.01 |
| **equity change** | **−2.824322** | **−2.824322** |

**The gap on this sample is zero to the cent.** That is the strongest possible
evidence that backtest and paper share fill logic: they are the *same*
`quantis_backtest::FillEngine` and the *same* `quantis_risk::RiskGate`, so on
identical data they can only differ through execution *timing*.

## Why it is zero here — and when it would not be

The two paths do differ in one way (ADR-004): the **backtester delays execution
to the next snapshot**, while the **paper gateway fills against the latest book
at submission**. On this sample that difference does not move any fill price,
because the sample is calm — between the signal snapshot and the next, the touch
prices are unchanged (the same reason sub-500ms latency is a no-op here, §2 of
`losing-money.md`). So the timing difference exists but has no price impact, and
the equity changes coincide exactly.

On **volatile** data the gap would open, and its sources, in expected order of
size, are:

1. **Execution timing.** Next-snapshot (backtest) vs. submission-time (paper)
   fills diverge whenever the touch moves between snapshots. This is the
   dominant term on active data and is *modelled*, not accidental.
2. **Real network latency (live paper only).** Replay has none; live paper pays
   true round-trip latency the backtest cannot see at snapshot resolution — a
   lower bound becomes a real cost.
3. **Partial fills / depth.** If an order exceeds visible depth, the timing of
   when depth refreshes differs between the two paths.
4. **Funding timing.** Funding is applied at interval boundaries; tiny
   differences in which events straddle a boundary can shift accrual by one
   interval.

## How to reproduce and watch the gap grow

Re-run both on a self-captured *volatile* window (`quantis record` during an
active session) and compare equity changes. The gap should be small but
non-zero, and dominated by term (1). If it is large or has an unexplained
component, that is a bug in one of the two paths — which is exactly what this
comparison is designed to surface.

## What this does and does not prove

- **Proves:** the fill and risk logic are genuinely shared — no separate
  "backtest math" that flatters results.
- **Does not prove:** that paper matches *live testnet*. That gap (real latency,
  real queue position, real partial fills, exchange-side rejects) is larger and
  is measured separately once testnet placement is enabled (§6 of the runbook).
  Paper is the floor of realism, not the ceiling.
