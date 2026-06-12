# Contributing

## Prerequisites

- Stable Rust via [`rustup`](https://rustup.rs/) (edition 2024 workspace)
- [`uv`](https://docs.astral.sh/uv/) for the Python environment (it will
  provision Python 3.11 automatically)

## Setup

```sh
make setup    # syncs python/.venv and installs pre-commit hooks
```

## Everyday commands

| Command | What it does |
|---------|--------------|
| `make fmt` | Auto-format Rust + Python |
| `make lint` | clippy (warnings = errors), ruff, mypy strict |
| `make test` | cargo test + pytest |
| `make ci` | Exactly what CI runs, locally |
| `make demo` | Current phase's end-to-end demo |

## Quality gates

CI (`.github/workflows/ci.yml`) runs Rust fmt/clippy/tests, Python
ruff/mypy/pytest, and a deterministic smoke job on every PR and on `main`.
Enable branch protection on `main` requiring all three jobs, so merges are
blocked on red.

## Commit conventions

[Conventional commits](https://www.conventionalcommits.org/): `feat:`,
`fix:`, `build:`, `ci:`, `docs:`, `test:`, `refactor:`, `perf:`, `chore:`,
with an optional crate/layer scope (e.g. `feat(risk): ...`). One logical
change per commit, imperative mood, body explains *why* when it isn't
obvious. No "fix stuff".

## Architecture decisions

Any decision that crosses the Rust/Python boundary, affects statistical
integrity (data handling, CV, evaluation), or touches the safety posture
requires an ADR in `docs/adr/` (use `template.md`). Superseded ADRs are
never deleted — their status changes.

## Safety rules

- **Never commit secrets.** Keys live in environment variables or gitignored
  config (`config/engine.toml`, `.env`). The gitleaks pre-commit hook scans
  every commit; treat a hook failure as a stop-everything event.
- **Mainnet support is out of scope by policy**, not by omission. Do not add
  a bypass for the `mode = "mainnet"` rejection.

## Process

`PROGRESS.md` is the live build ledger (phase status, assumptions, next
action). Keep it current in every PR that changes scope or completes a unit.
