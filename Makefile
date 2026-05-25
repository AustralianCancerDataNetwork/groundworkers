.PHONY: setup test describe

PYTHON := .venv/bin/python3
CONFIG ?= config/cava-mcp.example.yaml

setup:
	uv sync --extra dev --extra embedding-tools

describe:
	$(PYTHON) -m cava_mcp.server --config $(CONFIG) --describe

test:
	$(PYTHON) -m pytest
