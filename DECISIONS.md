# DECISIONS

> **GitHub "About" line:** Research-to-execution engine for regime-switching
> strategies on Hyperliquid perps — Rust market-data/backtest/paper-trading
> core under a Python research layer with statistically honest evaluation.
> Paper/testnet only.

Judgment calls made during the public-release readiness pass (2026-07-02),
in the spirit of [ADR-000](docs/adr/ADR-000-record-architecture-decisions.md):
decisions that *cross-cut* the release rather than the architecture live here.

## Code & tooling

- **mypy 2.x regression fixed with annotations, not overrides.** The locked
  toolchain (mypy 2.1, numpy 2.4 stubs) started inferring `ndarray.mean(axis=)`
  as `Any`, tripping `no-any-return` in `quantis/evaluation/cscv.py`. Fixed by
  annotating the three locals with the module's own `Array` alias — type-true,
  no suppression, no behavior change.
- **`benchmarks/` brought under the lint umbrella.** `benchmarks/book_bench.py`
  sat outside the `python/` lint root and was never checked by ruff/mypy; it
  carried a dead placeholder assignment and four E741 `l` variables. Cleaned,
  and the Makefile + CI python job now format/lint/type-check `../benchmarks`
  too, so the gap cannot reopen.
- **`PROGRESS.md` kept, de-scaffolded.** The build ledger is referenced by the
  README and CONTRIBUTING as a feature, so it stays — but AI-session resumption
  scaffolding ("On CONTINUE…", shell-CWD notes), a stale duplicate "NEXT
  ACTION" still pointing at Phase 2, original-plan sections still marked
  `[TODO]` despite being done, and a machine-local absolute path were all
  removed or resolved. Open items are now `[DEFERRED]` with their external
  blockers stated.

## Packaging

- **`publish = false` across the Cargo workspace.** This is an application
  workspace (binary + PyO3 wheel), not a library family for crates.io; the
  internal path-dependencies make `cargo publish` meaningless anyway. Marking
  every crate unpublishable is the fail-closed default this repo prefers.
  "Pack succeeds locally" is therefore demonstrated by `cargo build --release`,
  `uv build` (quantis sdist+wheel), and `maturin build` (quantis-core wheel) —
  all verified.
- **Authors metadata is "Quantis contributors"**, matching the LICENSE
  copyright line, rather than a personal name/email — keeps personal info out
  of the tree.
- **Declared MSRV is 1.87.** Initially set to 1.85 (the edition-2024 floor);
  clippy's `incompatible_msrv` lint — caught in the fresh-clone verification —
  showed the code uses `is_multiple_of` (stable 1.87). 1.87 is the lowest
  version the workspace actually compiles-and-lints clean on.
- **Version stays 0.1.0 everywhere** (workspace, both pyprojects), matching the
  CHANGELOG's initial release entry. The CHANGELOG's release-tag link becomes
  real when the maintainer pushes and tags `v0.1.0`.

## Hygiene & history (reviewed, nothing rewritten)

- **No secrets in the tree or in git history.** Every commit has passed the
  gitleaks pre-commit hook since Phase 0; an independent pattern scan over all
  history diffs (API keys, AWS/GitHub/Slack tokens, private-key blocks, 64-hex
  wallet keys) found nothing. No history rewrite needed.
- **One machine-local path existed in history** (`/Users/…` in PROGRESS.md,
  from the initial ledger commit). Removed from the tree; left in history —
  it is not a secret and rewriting history for it is not worth invalidating
  clones. ⚠️ **For the human reviewer:** commit *metadata* carries the
  maintainer's name and personal email address (normal for OSS, and visible on
  any push under your account). If you would rather publish under a GitHub
  noreply address, that requires a history rewrite **before** the first public
  push — decide now, not later.
- **`.claude/` added to `.gitignore`.** Local agent settings (which contain
  absolute paths) were untracked but unignored; now ignored so they can never
  land in a commit.

## Docs

- **README quickstart made copy-paste runnable from a fresh clone.** The paper
  trading commands invoked a bare `quantis` binary and referenced
  `config/engine.toml`, which is gitignored and absent in a clone. The
  quickstart now builds the binary, copies the example config, and calls the
  built path explicitly; the runbook states the same prerequisites.
