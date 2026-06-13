# Sample data provenance

## `btc-sample.qnts`

- **Instrument:** BTC perpetual, Hyperliquid **mainnet** public feed.
- **Captured:** 2026-06-13 ~01:48–02:03 UTC (15-minute window).
- **Capture tool:** `quantis record` (this repo) — the same ingestion and
  recording code the live engine uses, not a separate scraper.
- **Contents:** 3,077 normalized events — 1,669 L2 snapshots, 1,408 trade
  prints. Hyperliquid pushes the `l2Book` feed at most ~2/s per block.
- **Format:** length-prefixed bincode event log (magic `QNT1`); see
  `crates/market-data/src/recorder.rs`.
- **SHA-256:** `fcc4ae84394e041aa0ecced15fc94858660414d75dd52fd4997340636277e876`
- **Size:** ~1.4 MB.

## Why this file is committed

The README promises an offline backtest+dashboard demo in under five minutes.
A small, real, hash-pinned slice makes that demo reproducible with no network
and no exchange account, and it anchors the CI **golden-hash smoke test**
(`tests/smoke/expected_hash.txt`): the seeded backtest over this exact file
must reproduce one fixed determinism hash, so any change to fill logic,
fixed-point math, or data handling that alters results fails the build.

## Quality notes (from the capture, honestly)

- Zero dropped events, zero parse errors, one connection with no reconnects
  over the window — the feed was healthy.
- Book integrity on replay: **0** crossed, unsorted, bad-quantity, or
  timestamp-regression events.
- Feed latency `recv - exch` measured p50 ≈ 350 ms, p99 ≈ 1.7 s. This figure
  **includes wall-clock skew** between the capture machine and the exchange
  (the two clocks are not synchronized), so it is an upper bound on true
  network latency, not a clean measurement of it. Reported, not hidden.

## `btc-1d-candles.csv`

- **Instrument:** BTC perpetual, Hyperliquid mainnet.
- **Source:** Hyperliquid `info` endpoint, `candleSnapshot`, interval `1d`.
- **Span:** 2023-01-01 → 2026-06-13 (1,260 daily candles).
- **Columns:** `open_ms,open,high,low,close,volume`.
- **SHA-256:** `8580eaa005b7c37d692e3f1bbeeea3eac4127c72244f48d4d98e329e238f6c3f`
- **Why bundled:** the regime research dashboard and the one-shot holdout need
  longer *bar* history than 15 minutes of L2 can provide. Daily candles over
  ~3.5 years span a real bear bottom (~$16.5k, Jan 2023), recovery, and later
  ranges — enough for the regime models to find economically meaningful states.
- **Quality note:** the earliest candles carry `volume = 0` (Hyperliquid
  backfilled OHLC before its own volume existed). Close prices are real BTC, so
  return-based regime analysis is unaffected; volume-based features on the early
  span would not be, and are not used.

## Limitations

- 15 minutes is enough to exercise the engine end to end and to support the
  demo and smoke test; it is **far too short** for any statistical claim
  about a strategy. Real research data is captured separately into the
  gitignored `data/capture/` (see `data/README.md`) or fetched from
  Hyperliquid's candle history.
- Snapshot feed only: no per-order queue position is recoverable from L2
  snapshots, which is one reason the Phase 3 fill model treats resting-order
  queue position conservatively (ADR-004).
