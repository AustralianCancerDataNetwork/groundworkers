.PHONY: setup test lint typecheck check describe inspector

PYTHON := .venv/bin/python3
# Empty = the server-specific lookup (OA_CONFIG_PATH, then ~/.config/omop/config.toml).
# Override per run, e.g. make describe CONFIG=config/groundworkers.example.toml
CONFIG ?=

# Mirrors the install CI performs, so a local run reproduces the pipeline exactly.
setup:
	uv sync --all-extras --dev

describe:
	$(PYTHON) -m groundworkers.server $(if $(CONFIG),--config-path $(CONFIG)) --describe

# Launch the official MCP Inspector against this checkout's stdio server.
inspector:
	MCP_INSPECTOR_SECRET_STORE=memory npm_config_cache="$${TMPDIR:-/tmp}/groundworkers-inspector-npm-cache" npx --yes @modelcontextprotocol/inspector@2.12.0 uv run --project . groundworkers

lint:
	uv run ruff check .

typecheck:
	uv run ty check src/

test:
	uv run pytest -q

# The three gates build-test.yml enforces on every pull request.
check: typecheck lint test
