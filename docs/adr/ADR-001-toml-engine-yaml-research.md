# ADR-001: TOML for the engine, YAML for research

- Status: accepted
- Date: 2026-06-11

## Context

Everything in Quantis is config-driven — no magic numbers in code — so the
config formats are load-bearing. Two consumers with different shapes: the
Rust engine (flat, operational: mode, seed, paths, logging) and the Python
research layer (nested, iterated on daily: feature pipelines as lists of
parameterized specs, model grids, CV schemes).

## Decision

Two formats, one philosophy:

- **TOML for the engine** (`config/engine.example.toml`), deserialized by
  serde into `quantis_core::config::EngineConfig`.
- **YAML for research** (`config/research.example.yaml`), validated by
  pydantic into `quantis.config.ResearchConfig`.

The shared philosophy is fail-closed and is enforced in both schemas:
unknown fields are hard errors, every run carries an explicit seed, and each
example config is parsed by its test suite so examples cannot drift from
schemas.

## Alternatives considered

- **TOML everywhere** — TOML's syntax for deep lists of tables is noisy
  exactly where research configs live (feature pipelines); daily-iterated
  files should be pleasant to edit.
- **YAML everywhere** — `serde_yaml` is deprecated/unmaintained, and YAML's
  implicit-typing footguns are a poor fit for an operational config where a
  surprising parse could change trading behavior. TOML is the Rust
  ecosystem's operational default with excellent error messages.
- **JSON** — no comments; commented examples are part of the documentation
  strategy.

## Consequences

Two schemas to keep honest instead of one. Accepted because the schemas
serve genuinely different shapes, and the drift risk is mitigated where it
matters: both are strict, both examples are under test, and any key that
must exist on both sides (e.g. the seed) gets an explicit cross-language
consistency test once the PyO3 boundary lands in Phase 2.
