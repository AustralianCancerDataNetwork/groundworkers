.PHONY: setup test lint typecheck check describe

PYTHON := .venv/bin/python3
CONFIG ?= config/groundworkers.example.toml

# Mirrors the install CI performs, so a local run reproduces the pipeline exactly.
setup:
	uv sync --all-extras --dev

describe:
	$(PYTHON) -m groundworkers.server --config $(CONFIG) --describe

lint:
	uv run ruff check .

typecheck:
	uv run ty check src/

test:
	uv run pytest -q

# The three gates build-test.yml enforces on every pull request.
check: typecheck lint test
