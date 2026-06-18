# Quantis developer entry points. Run `make help` for a listing.

PY_DIR := python
ENGINE_EXAMPLE := config/engine.example.toml

.PHONY: help setup fmt fmt-check lint test test-rust test-python ci demo smoke bench bindings research

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Sync the Python env, build the Rust extension, install pre-commit hooks
	cd $(PY_DIR) && uv sync --group dev
	$(MAKE) bindings
	uvx pre-commit install

bindings: ## Build the Rust PyO3 extension into the Python venv (maturin develop)
	cd $(PY_DIR) && uv run maturin develop --release -m ../crates/python/Cargo.toml

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

demo: ## Seeded backtest on the bundled sample + render the research dashboard (offline, <5 min)
	cargo run --release -q -p quantis-cli -- backtest --config $(ENGINE_EXAMPLE)
	cd $(PY_DIR) && uv run python scripts/render_dashboard.py
	@echo "Open results/dashboard.html in a browser."

research: ## Run all honest research studies + render the consolidated report (offline)
	cd $(PY_DIR) && uv run python scripts/regime_search.py
	cd $(PY_DIR) && uv run python scripts/ensemble_eval.py
	cd $(PY_DIR) && uv run python scripts/sizing_eval.py
	cd $(PY_DIR) && uv run python scripts/short_eval.py
	cd $(PY_DIR) && uv run python scripts/research_report.py
	@echo "Open results/research-report.html in a browser."

smoke: ## Deterministic backtest must reproduce the committed golden hash
	cargo run --release -q -p quantis-cli -- backtest --config $(ENGINE_EXAMPLE) \
		--expect-hash $$(cat tests/smoke/expected_hash.txt)

bench: ## Criterion benchmarks (book ladders, backtest loop)
	cargo bench -p quantis-market-data -p quantis-backtest
