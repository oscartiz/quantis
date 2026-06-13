# Runbook — operating the live engine

How to start, monitor, kill, and recover the paper/testnet trading engine.
This is an operations document: terse, imperative, and exact.

> **Safety.** The engine trades **paper/testnet only**. `mode = "mainnet"` is
> rejected by the config layer with no bypass. Testnet live order placement is
> additionally gated behind operator-supplied keys (see §6).

## 0. Prerequisites

- Built binary: `cargo build --release -p quantis-cli` → `target/release/quantis`
- A validated config: `quantis config validate config/engine.toml`
- For live data: outbound network to `wss://api.hyperliquid.xyz/ws` (public,
  keyless).

## 1. Start

**Offline paper (deterministic, no network):**
```sh
quantis trade --config config/engine.toml \
  --replay data/sample/btc-sample.qnts --metrics-port 9898
```

**Live paper (real data, simulated fills):**
```sh
quantis trade --config config/engine.toml \
  --duration-secs 3600 --metrics-port 9898
```

The process logs structured events (`tracing`) to stdout — `format = "json"` in
`[logging]` for machine collection, `pretty` for a human.

## 2. Monitor

- **Metrics:** `curl -s localhost:9898/metrics` (Prometheus text). Key series:
  - `quantis_position_qty`, `quantis_equity_usd` — gauges; the live state.
  - `quantis_orders_submitted_total`, `_filled_total`, `_rejected_total`.
  - `quantis_feed_reconnects_total` — rising means an unstable feed.
  - `quantis_reports_duplicate_total` — duplicates ignored (idempotency working).
- **Dashboard:** import `docs/grafana-dashboard.json` into Grafana pointed at a
  Prometheus that scrapes `localhost:9898`.
- **Healthy looks like:** equity flat-to-trending, position within caps,
  reconnects near zero, rejections only when you expect risk vetoes.

## 3. Kill

- **Graceful:** `Ctrl-C` (SIGINT) — the process stops consuming events and
  prints a final position/PnL summary. Live mode also stops at `--duration-secs`.
- **Hard:** `kill -9 <pid>`. Safe for paper (no external state). For testnet,
  prefer graceful so in-flight cancels are sent; on hard kill, reconcile on
  restart (§5).
- **Risk kill switch (automatic):** if portfolio drawdown reaches the configured
  fraction, the `RiskGate` latches and **vetoes every risk-increasing order**
  while still allowing de-risking. The process keeps running (so you can
  flatten) but opens no new exposure. `quantis_orders_rejected_total` climbs and
  logs show `KillSwitchTripped`. The switch does **not** auto-reset — that is an
  operator decision after investigation.

## 4. The chaos drill (verify recovery before you need it)

The idempotency that makes recovery safe is covered by an automated test:
```sh
cargo test -p quantis-execution --test chaos
```
It kills the feed mid-order, reconnects, re-delivers and reorders fills, and
resubmits orders with the same client ids, asserting the tracked position is
unchanged (no phantom exposure) and that a genuinely missed fill is caught as
drift. Run it after any change to the order manager.

## 5. Recover

After any disconnect or restart:

1. **Reconcile.** On reconnect the engine compares its local position to the
   exchange's (`reconcile`). If they agree (`in_sync`), resume. If they drift,
   the **exchange is authoritative** — adopt its position, do not trade on the
   stale local view, and investigate the gap before re-enabling new orders.
2. **Replay is safe.** Re-delivered fills (same exchange fill id) and resent
   orders (same client id) are ignored by construction, so a reconnect that
   re-streams recent events cannot double-count.
3. **Resume** only once reconciled and the feed is stable
   (`quantis_feed_reconnects_total` stops climbing).

## 6. Testnet keys (gated)

Testnet live placement requires an operator-supplied testnet wallet key in the
environment (never in the repo; the gitleaks pre-commit hook enforces this).
Without it, `quantis trade` against `mode = "testnet"` exits with a loud gated
error and points here. The signing seam and the exact request format are in
`crates/execution/src/testnet.rs` and ADR-006.

## Quick reference

| Action | Command |
|---|---|
| Validate config | `quantis config validate config/engine.toml` |
| Paper (offline) | `quantis trade --config … --replay data/sample/btc-sample.qnts` |
| Paper (live) | `quantis trade --config … --duration-secs 3600` |
| Scrape metrics | `curl -s localhost:9898/metrics` |
| Chaos drill | `cargo test -p quantis-execution --test chaos` |
| Graceful stop | `Ctrl-C` |
