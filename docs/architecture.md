# Architecture

Quantis is a two-language system with a hard performance boundary: a Rust core
owns the per-event hot path (ingestion, order book, matching, execution, risk),
and a Python layer owns research (features, regime models, evaluation, the
dashboard). They meet at two narrow interfaces — recorded event logs and a thin
PyO3 binding — and nowhere else. The boundary is justified with measured numbers
in [ADR-002](adr/ADR-002-rust-python-boundary.md).

## C1 — System context

```
            ┌────────────────────┐         public WS / REST (keyless)
            │  Hyperliquid        │◀───────────────────────────────────┐
            │  (mainnet data,     │                                     │
            │   testnet orders)   │◀──── signed orders (testnet, gated) │
            └────────────────────┘                                     │
                      ▲                                                 │
                      │                                                 │
            ┌─────────┴───────────────────────────────────────────────┴─┐
            │                        Quantis                              │
            │   Rust core (hot path)  ◀── event logs ──▶  Python research │
            │                          ── PyO3 binding ─▶                 │
            └────────────────────────────────────────────────────────────┘
                      │                         │
                  operator                  researcher
              (runbook.md: trade,        (dashboard, holdout,
               monitor, kill)             evaluation)
```

## C2 — Containers / crates

Rust workspace (`crates/`):

| crate | responsibility |
|---|---|
| `core` | fixed-point types, event model, fail-closed config, hashing, stats |
| `market-data` | HL WebSocket feed (reconnect/backoff/watchdog), order book, event-log recorder |
| `backtest` | event-loop engine + **the matching/fill engine** (single source of truth), results artifact |
| `risk` | sizing (vol-target, capped Kelly) + the integer pre-trade gate + kill switch |
| `execution` | order state machine, paper gateway, testnet gateway, reconciliation, metrics |
| `python` | PyO3 bindings exposing the core as the `quantis_core` extension |
| `cli` | the `quantis` binary: record / replay / backtest / trade |

Python package (`python/quantis/`):

| module | responsibility |
|---|---|
| `data` | candle + event-log loaders, the holdout wall |
| `features` | causal feature pipeline + leakage canary |
| `models` | Gaussian HMM (own EM) and BOCPD |
| `evaluation` | purged CV, DSR, SPA, trial log |
| `dashboard` | static HTML research report |

## C3 — The two crossing points (and nothing else)

1. **Event logs** (`market-data::recorder`). The recorder writes a
   length-prefixed binary log; the backtester and the Python `read_mid_series`
   both read it. This is how research gets data without touching the live path.
2. **PyO3 binding** (`crates/python`). Python *launches* Rust runs and *reads*
   their artifacts; it never sits inside the per-event loop. `run_backtest`
   calls the exact same `quantis_backtest::runner` the CLI calls — so a
   Python-driven backtest reproduces the CLI's determinism hash byte for byte
   (asserted by a cross-language test).

The single-source-of-truth rule is structural: the matching engine lives in
`backtest`, and both the backtester and the `execution::paper` gateway consume
it. There is no second fill implementation to drift.

## Data / event flow

### Backtest (offline, deterministic)

```
event log ──▶ backtest::engine loop, per event:
                 1. apply L2 snapshot to the order book
                 2. accrue funding at interval boundaries
                 3. fill ARRIVED orders vs book   (FillEngine — shared)
                 4. run strategy(book, position)  → order intents
                 5. queue intents with arrival = now + latency
                 6. mark-to-market equity
              ──▶ RunSummary ──▶ ResultsArtifact (hashed: config+data+metrics)
```

Everything except wall-clock timing is integer-exact, so the artifact's
`determinism_hash` is reproducible across machines and is asserted by CI.

### Live / paper (real time)

```
HL WebSocket ──▶ market-data::ws (reconnect, watchdog, bounded channel w/ drops)
              ──▶ normalized MarketEvent ──▶ trade loop:
                    gateway.on_event(book update + risk equity mark)
                    strategy → intents → RiskGate.check_order (veto?)
                       └─ allowed ──▶ gateway.submit
                    gateway.poll_reports ──▶ OrderManager (idempotent)
                    metrics.set_position / set_equity
              ──▶ /metrics (Prometheus) ──▶ Grafana
```

The paper gateway routes through the **same** `FillEngine` and `RiskGate` as the
backtest, so backtest↔paper divergence is data + timing only
([backtest-paper-gap.md](backtest-paper-gap.md)).

### Research

```
candles / event log ──▶ features (causal) ──▶ HMM / BOCPD
   ──▶ smoothed regimes (analysis overlay) + filtered regimes (causal signal)
   ──▶ strategy returns ──▶ metrics (Sharpe/Sortino/DD), DSR, SPA
   ──▶ dashboard.html   |   holdout (sealed, evaluated once)
```

## Determinism and reproducibility

- Prices/sizes are `i64` fixed-point (1e8); all hot-path arithmetic is integer,
  so results hash identically across platforms.
- Every run is seeded and fully config-driven; the backtest emits a versioned
  artifact (config hash + data hash + git SHA + integer metrics).
- The holdout boundary and content hash are committed before evaluation.

## Safety posture in the architecture

- `mode = "mainnet"` is rejected at config load with no bypass (`core::config`).
- The testnet gateway needs an operator-supplied key *and* an `ActionSigner`
  *and* transport; without them, submission is loudly gated
  ([ADR-006](adr/ADR-006-execution-and-gateways.md)).
- The risk gate can veto any order and latches a kill switch on drawdown; it
  sits below both gateways so the guarantee is identical for paper and testnet.
