.PHONY: setup test describe

PYTHON := .venv/bin/python3
CONFIG ?= config/groundworkers.example.yaml

setup:
	uv sync --extra dev --extra embedding-tools

describe:
	$(PYTHON) -m groundworkers.server --config $(CONFIG) --describe

test:
	$(PYTHON) -m pytest
