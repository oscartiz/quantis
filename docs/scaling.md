# Scaling: from one strategy / one venue to many

Quantis ships single-asset (BTC), single-venue (Hyperliquid), single-strategy.
This document is the honest map of what changes — and what does not — as that
grows, with the load-bearing work called out rather than hand-waved.

## What is already built for scale

- **The event model is instrument-agnostic.** `MarketEvent` carries no symbol;
  the instrument is named once in an event-log header. A second instrument is a
  second stream, not a schema change.
- **Fixed-point types and the matching engine are per-book**, so running N books
  is N instances, not a rewrite.
- **The risk gate is portfolio-shaped already** in spirit (caps on position and
  notional, a portfolio drawdown limit); it generalizes to per-instrument caps
  plus a portfolio aggregate with bounded extra state.

## Axis 1 — More instruments (BTC → BTC, ETH, SOL, …)

What changes:

- **Feed fan-out.** One WebSocket connection multiplexes subscriptions; the
  `market-data` client already handles multiple subscriptions per connection.
  Bounded channels become per-instrument (or a tagged single channel) to keep
  drop accounting per book.
- **Portfolio risk becomes real, not nominal.** The gate gains per-instrument
  caps *and* a portfolio aggregate (gross/net exposure, correlation-aware
  drawdown). This is the main new logic; it is a superset of the current gate,
  not a replacement.
- **Cross-asset regime research.** The HMM/BOCPD comparison becomes far richer
  with several assets — shared vs idiosyncratic regimes. The feature pipeline
  and CV machinery are unchanged; only the data dimensionality grows.

What does *not* change: the matching engine, the fill model, the determinism and
reproducibility guarantees, the holdout discipline.

## Axis 2 — Higher fidelity data (snapshots → tick/L3)

This is the single most important upgrade, because it lifts the honest ceiling
documented in [ADR-004](adr/ADR-004-backtest-fill-model.md) and
[losing-money.md](losing-money.md): at ~2 snapshots/second, sub-500ms latency is
below resolution and queue position is unobservable.

- **Deep-L2 / L3 backfill** from Hyperliquid's requester-pays S3 archive (or a
  vendor like Tardis) gives per-order events. With them, the latency model
  resolves true microsecond delays and the conservative back-of-queue maker
  model becomes a *measured* queue position.
- The recorder format already length-prefixes frames; a richer event variant
  slots in behind the same reader, and the backtest loop is agnostic to which
  event variants it consumes.

Until then, **backtested latency and maker-fill costs are lower bounds**, stated
as such and treated as sensitivity axes rather than settled numbers.

## Axis 3 — More strategies

- The `Strategy` trait (Rust) and the research strategies (Python) are
  independent; adding one is additive. The config grows from a single
  `[backtest.strategy]` table to per-strategy tables (the schema comment already
  anticipates this).
- **Multiple-testing pressure rises with strategy count** — which is exactly
  what the trial log + DSR + SPA are for. More strategies make the deflation
  *more* important, not less; the machinery is already in place.

## Axis 4 — More venues

- `OrderGateway` is the seam: a new venue is a new gateway implementation, and
  the trading loop is venue-agnostic. The testnet gateway shows the shape
  (request construction + a signing seam).
- Cross-venue concerns appear: a normalized symbology layer, per-venue fee and
  funding schedules (already config), and cross-venue netting in the risk
  aggregate. None touch the core event loop.

## Operational scaling

- **Throughput headroom is large.** The backtest loop runs at ~14M events/s
  single-threaded (ADR-002); many instruments fit comfortably before
  parallelism is needed, and books are independent so sharding is natural.
- **Observability already generalizes**: metrics are labelled by intent; adding
  per-instrument labels is a small change, and the Grafana dashboard templates
  over a datasource variable.
- **State and recovery**: the idempotent order manager and reconciliation scale
  per-instrument; the chaos-test invariant (no phantom position on replay) holds
  per book.

## What would need genuine rework

Honesty about the hard parts:

- **Cross-instrument atomicity** (e.g. a basket or a hedge that must fill
  together) is not in the current single-order model and would need a new
  order-group abstraction.
- **A true L3 queue simulation** is a different fidelity class from the snapshot
  book and is real work, not a config flag.
- **Capacity modelling** beyond single-order book-walk (market impact decay,
  participation-rate limits) is research in itself.

The guiding principle: the *event loop, fill engine, risk gate, and determinism
guarantees* are the stable core; scaling adds instruments, data fidelity,
strategies, and venues *around* it, and the places that need real new design are
named above rather than implied to be free.
