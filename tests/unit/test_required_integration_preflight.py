from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from tests.conftest import _missing_required_tests, _required_test_failures
from tests.integration import test_omop_graph_adapter as graph_integration


@pytest.fixture(autouse=True)
def required_integration_config(monkeypatch):
    monkeypatch.setenv("GROUNDWORKERS_REQUIRE_INTEGRATION", "1")
    monkeypatch.setenv("GROUNDWORKERS_CONFIG_PATH", "/fixture/stack.toml")


def test_missing_graph_dependency_fails_required_preflight(monkeypatch):
    original_import = builtins.__import__

    def missing_graph(name, *args, **kwargs):
        if name == "omop_graph":
            raise ImportError("fixture omitted omop-graph")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_graph)

    with pytest.raises(RuntimeError, match="omop-graph installed"):
        graph_integration._load_graph_adapter()


def test_bad_config_fails_required_preflight(monkeypatch):
    def invalid_config(**_kwargs):
        raise ValueError("invalid fixture config")

    monkeypatch.setattr(graph_integration, "build_app_config", invalid_config)

    with pytest.raises(ValueError, match="invalid fixture config"):
        graph_integration._load_graph_adapter()


@pytest.mark.parametrize(
    ("adapter", "message"),
    [
        (None, "adapter was not built"),
        (SimpleNamespace(is_available=lambda: False), "backend is not available"),
    ],
)
def test_missing_or_unavailable_adapter_fails_required_preflight(
    monkeypatch, adapter, message
):
    monkeypatch.setattr(graph_integration, "build_app_config", lambda **_kwargs: object())
    monkeypatch.setattr(
        graph_integration,
        "build_adapters",
        lambda _config: SimpleNamespace(omop_graph=adapter),
    )

    with pytest.raises(AssertionError, match=message):
        graph_integration._load_graph_adapter()


def test_missing_seeded_fixture_concept_fails_required_preflight():
    class IncompleteAdapter:
        def get_concept(self, concept_id):
            return {"concept_id": concept_id} if concept_id == 201826 else None

    with pytest.raises(AssertionError, match="201837"):
        graph_integration._require_seeded_concepts(IncompleteAdapter(), (201826, 201837))


def test_execution_gate_detects_a_disabled_required_test():
    required = {"graph::test_required", "emb::test_required"}

    assert _missing_required_tests(required, {"graph::test_required"}) == [
        "emb::test_required"
    ]
    assert _required_test_failures(required, {"graph::test_required": "passed"}) == [
        "emb::test_required"
    ]
