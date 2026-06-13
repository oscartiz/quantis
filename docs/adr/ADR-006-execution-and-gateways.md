# ADR-006: Execution gateways and the testnet signing seam

- Status: accepted
- Date: 2026-06-13

## Context

Phase 4 needs the system to *place* orders, not just simulate them, while
preserving the safety posture (paper/testnet only, no real capital) and the
core architectural claim (backtest and live share fill + risk logic). Two
questions had to be answered: how to structure the venue abstraction, and how
far to go implementing the Hyperliquid exchange signing without testnet keys to
verify it.

## Decision

### One gateway trait, venue-agnostic loop

All venues implement `OrderGateway` (submit / cancel / poll_reports / position).
The trading loop is written once against the trait, so switching paper → testnet
is a config change, not a code path. The trait is small and pull-based
(`poll_reports`) so the caller owns the event loop and timing.

### The order manager is the safety core

`OrderManager` applies execution reports **idempotently**: fills dedupe by
exchange fill id, client order ids dedupe resubmissions, and every status
transition is validated. This is what makes reconnect-and-replay safe (the
chaos test), and it lives below both gateways so the guarantee is identical for
paper and testnet.

### Paper gateway reuses the real engines

`PaperGateway` matches orders with `quantis_backtest::FillEngine` and vets them
with `quantis_risk::RiskGate` — the *same* code the backtester and the live
risk path use. Consequence, measured: on the bundled sample the paper equity
change equals the backtest net PnL to the cent (`docs/backtest-paper-gap.md`).

### Testnet signing is a documented seam, not a hand-rolled signer

This is the load-bearing honesty decision. Hyperliquid's exchange endpoint
(`POST /exchange`, verified 2026-06-13) takes `{action, nonce, signature}`,
where the signature is an **EIP-712 signature over an msgpack hash of the
action**, produced with a secp256k1 wallet key. We:

- **Implement and test** everything that does not need a key: the order action
  (`a/b/p/s/r/t/c` fields), the IOC-limit encoding of a market order, the
  128-bit `cloid`, and the request envelope. Unit tests assert the serialized
  shape against the documented format.
- **Do not ship a signer.** `ActionSigner` is a trait the operator implements
  with their key and the official SDK's reference scheme. Without a signer,
  `TestnetGateway::submit` returns `GatewayError::Gated` — loudly, never
  silently.

## Alternatives considered

- **Hand-roll the msgpack + EIP-712 + secp256k1 signer in-repo.** Rejected.
  Without testnet keys it cannot be verified against the live exchange, and a
  signer that "looks right" but has never authenticated is worse than an honest
  seam — it invites someone to trust an untested auth path. A subtle hashing or
  domain-separator error would fail only in production.
- **Use a third-party Hyperliquid Rust SDK.** Reasonable future option, but it
  pulls a large dependency and an Ethereum signing stack into the core for a
  path we cannot exercise here; deferred until keys are available to validate it
  end to end.
- **Skip testnet entirely, paper-only.** Rejected: the request construction is
  real, testable, and valuable, and the seam makes the remaining work explicit
  rather than absent.

## Consequences

- The safety posture is strengthened, not weakened: placing a testnet order
  requires an operator to deliberately supply a key *and* implement a signer
  *and* wire transport. Three explicit steps, none of which can happen by
  accident, and mainnet stays rejected at the config layer regardless.
- The backtest↔paper equivalence is structural and measured; the paper↔testnet
  gap (real latency, queue position, exchange rejects) is acknowledged as larger
  and is the next thing to measure once keys exist.
- `docs/runbook.md` §6 and `crates/execution/src/testnet.rs` are the canonical
  pointers for an operator enabling testnet.
