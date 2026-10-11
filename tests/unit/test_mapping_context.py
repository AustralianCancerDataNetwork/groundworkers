import asyncio

import pytest

from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.plugins import MappingContext
from tests.support.stack_config import build_cdm_stack


class StubMappingService:
    def concept_candidate_bundle(self, query: str, **kwargs):
        return {"query": query, "kwargs": kwargs}

    async def async_concept_candidate_bundle(self, query: str, **kwargs):
        return {"query": query, "kwargs": kwargs, "async": True}

    def concept_mapping_context(self, concept_id: int, **kwargs):
        return {"concept_id": concept_id, "kwargs": kwargs}

    def concept_shared_standard_ancestor(self, concept_ids: list[int], **kwargs):
        return {"concept_ids": concept_ids, "kwargs": kwargs}


class StubGroundingService:
    def ground(self, query: str, **kwargs):
        return {"query": query, "kwargs": kwargs}


def test_mapping_context_delegates_to_the_host_service():
    context = MappingContext(mapping_service=StubMappingService())

    assert context.available is True
    assert context.candidate_bundle("diabetes", limit=3) == {
        "query": "diabetes",
        "kwargs": {"limit": 3},
    }
    assert context.concept_mapping_context(201826, include_ancestors=False) == {
        "concept_id": 201826,
        "kwargs": {"include_ancestors": False},
    }
    assert context.concept_shared_standard_ancestor([1, 2]) == {
        "concept_ids": [1, 2],
        "kwargs": {"max_depth": 5},
    }
    assert context.concept_shared_standard_ancestor([1, 2], max_depth=8) == {
        "concept_ids": [1, 2],
        "kwargs": {"max_depth": 8},
    }


def test_mapping_context_exposes_async_candidate_retrieval():
    context = MappingContext(mapping_service=StubMappingService())

    assert asyncio.run(context.async_candidate_bundle("diabetes")) == {
        "query": "diabetes",
        "kwargs": {},
        "async": True,
    }


def test_mapping_context_reports_unconfigured_service():
    context = MappingContext()

    assert context.available is False
    with pytest.raises(RuntimeError, match="mapping service is not configured"):
        context.candidate_bundle("diabetes")
    with pytest.raises(RuntimeError, match="mapping service is not configured"):
        context.concept_shared_standard_ancestor([1, 2])


def test_mapping_context_exposes_grounding_without_exposing_service_internals():
    context = MappingContext(grounding_service=StubGroundingService())

    assert context.grounding_available is True
    assert context.ground("diabetes", limit=3) == {
        "query": "diabetes",
        "kwargs": {"limit": 3},
    }
    assert not hasattr(context, "grounding_service")


class ContextPlugin:
    name = "context_plugin"
    config_cls = None

    def build(self, context, config):
        assert config is None
        return context.mapping_context

    def register(self, server, state):
        del server, state


def test_application_wires_one_shared_mapping_context_into_plugins(monkeypatch):
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [ContextPlugin()])

    app = build_application(build_app_config_from_stack(build_cdm_stack()))

    context = app.plugins["context_plugin"]
    assert isinstance(context, MappingContext)
    assert context.available is True
    assert context.grounding_available is True
