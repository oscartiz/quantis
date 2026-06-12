# Quantis developer entry points. Run `make help` for a listing.

PY_DIR := python
ENGINE_EXAMPLE := config/engine.example.toml

.PHONY: help setup fmt fmt-check lint test test-rust test-python ci demo

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Sync the Python env and install pre-commit hooks
	cd $(PY_DIR) && uv sync --group dev
	uvx pre-commit install

fmt: ## Auto-format Rust and Python
	cargo fmt --all
	cd $(PY_DIR) && uv run ruff format .
	cd $(PY_DIR) && uv run ruff check --fix .

fmt-check: ## Check formatting without writing
	cargo fmt --all --check
	cd $(PY_DIR) && uv run ruff format --check .

lint: ## Clippy (warnings are errors), ruff, mypy strict
	cargo clippy --all-targets -- -D warnings
	cargo check -p quantis-python
	cd $(PY_DIR) && uv run ruff check .
	cd $(PY_DIR) && uv run mypy quantis tests

test: test-rust test-python ## All tests

test-rust: ## Rust unit + integration tests
	cargo test

test-python: ## Python tests
	cd $(PY_DIR) && uv run pytest -q

ci: fmt-check lint test ## Everything CI runs, locally
	@echo "All CI checks passed."

demo: ## Phase 0: validate the example config end to end (becomes a full seeded backtest + dashboard by Phase 5)
	cargo run -p quantis-cli -- config validate $(ENGINE_EXAMPLE)
