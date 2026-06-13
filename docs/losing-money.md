# What would lose money in production, and why

This is the most important document in the repository. A backtest that only
advertises its best case is marketing; this is the opposite. Everything below
is a way Quantis — or any strategy run on it — could lose real money, with
numbers where the engine can produce them and honest mechanism where it cannot.

> The bundled demo strategy (SMA crossover) is **not** alpha and is labelled as
> such. On the sample it loses money. The point of this document is the
> *method* of being honest about losses, applied to that demo and to any
> strategy that follows it.

## 0. The demo already loses money (and that is the honest baseline)

On the committed 15-minute BTC sample, the demo strategy's result is:

| | net PnL | of which fees |
|---|---|---|
| baseline (taker 4.5 bps) | **−2.82** | −2.01 |

Gross of fees the strategy is already slightly negative (≈ −0.81); fees turn a
small loss into a larger one. This is the normal condition for a naive
technical strategy: **you are short the spread and the fees, and you need real
edge just to break even.** The sections below quantify how fragile "real edge"
is.

## 1. Transaction-cost sensitivity (quantified)

Fees are the most certain cost and the one most strategies underestimate.
Re-running the demo with the fee tier scaled up (engine `taker_fee_ppm`):

| fee level | taker bps | net PnL | Δ vs baseline |
|---|---|---|---|
| ×1 (baseline) | 4.5 | −2.82 | — |
| ×2 | 9.0 | −4.83 | −2.01 |
| ×3 | 13.5 | −6.84 | −4.02 |

The loss grows almost exactly linearly with fees, and the fee component is
**~71% of the baseline loss**. The reading: for a high-turnover strategy, the
fee schedule is not a footnote — it is the dominant term. A strategy that is
profitable at 1.5 bps maker rebate can be deeply unprofitable at 4.5 bps taker,
and **whether you pay maker or taker is itself a modelling assumption** the
backtest cannot verify (we conservatively assume taker for the demo). Funding
(modelled, see ADR-004) adds a further per-interval drag on held positions that
the 15-minute sample is too short to show but that dominates over days.

**Implication:** any strategy here must be evaluated at a fee level *above* the
exchange's quoted taker rate, and its P&L reported net of a funding assumption.
A strategy whose edge is smaller than 2× its round-trip cost is not real.

## 2. Execution latency and adverse selection

The fill model (ADR-004) never fills an order on the snapshot that triggered
it. But its resolution is bounded by the data:

| latency | net PnL on sample |
|---|---|
| 0 ms | −2.82 |
| 600 ms | −2.82 |
| 5 000 ms | −2.82 |
| 30 000 ms | −2.17 |

Sub-500ms latency is **below the snapshot feed's resolution** and is a no-op
beyond the structural one-snapshot delay. This is the honest ceiling: with
snapshot data, **backtested latency cost is a lower bound**. A strategy whose
edge lives at sub-second horizons cannot be validated here at all, and would
likely be arbitraged away by faster participants in production — a loss the
backtest is structurally blind to. Tick/L3 data is required to close this gap
(see `docs/scaling.md`).

## 3. Regime instability — the model's own assumptions failing

Both regime models encode assumptions that markets violate:

- **The Gaussian HMM** assumes a *fixed* number of *recurring* regimes with
  *constant* transition probabilities and Gaussian emissions. Real markets add
  new regimes (a first-of-its-kind deleveraging), shift transition rates, and
  produce fat tails and jumps that a Gaussian under-weights. When the regime
  structure the HMM learned in-sample stops holding, its state estimates become
  confidently wrong — the worst kind.
- **BOCPD** assumes piecewise-constant parameters with a *constant hazard*. Set
  the hazard too high and it over-segments noise into phantom regimes (shown in
  its tests on stationary data); too low and it is slow to flag a real break —
  precisely when being late is most expensive.
- **The smoothed HMM label is non-causal** (ADR-003). Using it as a trading
  signal is silent look-ahead bias; only the causal (filtered/BOCPD) signal is
  tradeable, and it is noisier. A strategy that looks great on smoothed labels
  and mediocre on causal ones is a strategy that will lose money live.

**Implication:** regime-based sizing can amplify losses exactly when the regime
model is most wrong, because that is when it is most confident and most
mistimed. This is not hypothetical — it is the standard failure mode of
regime-switching strategies.

## 4. Capacity limits

The demo trades 0.01 BTC and fills within the top level (zero `unfilled_qty`,
no multi-level walk). That tells us nothing about capacity, because capacity is
about *size you don't trade in the sample*. The mechanism:

- A market order walks the book; the fill model already charges this slippage
  and reports `unfilled_qty` when depth runs out. As order size approaches and
  exceeds top-of-book depth, average fill price degrades and the modelled edge
  shrinks — eventually to negative.
- For BTC perp on Hyperliquid, top-of-book depth is on the order of a few BTC;
  a strategy sized at single-digit BTC per trade begins to pay material impact,
  and one sized in the tens of BTC moves the market against itself.
- Capacity also interacts with turnover: a high-frequency strategy revisits the
  book often, so its *cumulative* participation — and thus impact and the
  chance of being detected and faded — is far higher than its per-order size
  suggests.

**Honest limitation:** the 15-minute sample cannot support a precise capacity
curve. We state the mechanism and flag capacity as a first-class risk; a real
estimate needs deep-book history and live participation measurement (Phase 4
paper-vs-backtest gap, `docs/scaling.md`).

## 5. Overfitting and multiple testing

The single most likely way to lose money is to *believe a backtest that was
selected for looking good*. Quantis builds the defences in, but they only work
if used:

- **Deflated Sharpe Ratio.** Searching N strategies inflates the best one's
  Sharpe by luck. The DSR (`quantis.evaluation`) deflates the Sharpe by the
  *expected maximum* across the trials actually logged. Demonstrated in tests:
  the best of 200 zero-edge strategies has a naive PSR > 0.9 but a **DSR < 0.7**
  — i.e. no real edge once you account for the search.
- **SPA / Reality Check.** Tests whether the best strategy beats the benchmark
  given the whole set. Demonstrated: it ignores pure noise (high p-value) yet
  detects a genuine injected edge (p < 0.05).
- **Purged, embargoed CV + the holdout wall.** Leakage at fold boundaries and a
  peeked holdout both manufacture fake edge; the CV guard and the seal-and-
  reveal-once holdout (`quantis.evaluation`, `quantis.data.holdout`) make both
  mechanically hard.

**Implication:** a Sharpe reported without a trial count, a DSR, and an
untouched-holdout number is not evidence. If the holdout number is mediocre,
that is the truth — and shipping it honestly is the entire point.

## 6. Things this backtest cannot see at all

Stated plainly so they are not mistaken for "handled":

- **Exchange/operational risk:** outages, socket gaps mid-position, liquidation
  cascades, oracle/funding anomalies, API rate-limit lockouts. The live engine
  mitigates (reconnect, reconciliation, kill switch — Phase 4) but cannot
  eliminate.
- **Non-stationarity of costs:** spreads widen and funding spikes exactly in
  the stressed conditions where a strategy most wants to trade.
- **Crowding:** a public edge decays as others trade it; the backtest assumes
  the past liquidity and the past edge persist.
- **Survivorship in instrument choice:** picking BTC because it is liquid *now*
  is a survivorship decision; the method must generalize to instruments chosen
  ex-ante.

## Bottom line

Run on this engine, a strategy loses money when its edge is smaller than its
round-trip cost (Section 1), when its edge lives below the data's time
resolution (Section 2), when its regime model is confidently wrong (Section 3),
when it is sized beyond book depth (Section 4), or when its backtest was
selected rather than validated (Section 5). The demo strategy fails the first
test outright, on purpose. The honest path to *not* losing money is a real edge
that survives all five — measured with the deflation and out-of-sample
machinery this repo ships, not asserted.
