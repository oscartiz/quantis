# ADR-000: Record architecture decisions

- Status: accepted
- Date: 2026-06-11

## Context

Quantis is built to be audited. A reviewer deciding whether this system can
be trusted with capital should be able to trace every load-bearing choice —
language boundary, fill model, statistical methodology, risk framework — to a
written rationale that names the alternatives and the trade-offs accepted.
Code review shows *what*; only contemporaneous records preserve *why*.

## Decision

Use lightweight ADRs (Nygard format, see `template.md`) in `docs/adr/`,
numbered chronologically by decision date. An ADR is required whenever a
decision:

1. crosses the Rust/Python boundary or a crate boundary,
2. affects statistical integrity (data handling, cross-validation,
   evaluation, holdout discipline), or
3. affects the safety posture.

ADRs are written when the decision is made, not retrofitted at the end.
Superseded ADRs keep their file; only the status line changes.

## Alternatives considered

- **Design doc per phase** — drifts from reality as phases evolve; ADRs are
  immutable point-in-time records, which is the property an auditor needs.
- **Rationale in code comments only** — scatters the why across files and
  loses the alternatives-considered section, which is where honesty lives.

## Consequences

Small, recurring writing overhead per major decision. In exchange, the design
history is reviewable, the why survives refactors, and claims like "the
boundary is justified with numbers" have a canonical place to be checked.
