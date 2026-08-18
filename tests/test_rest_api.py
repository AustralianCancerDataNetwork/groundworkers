from __future__ import annotations

from fastapi.testclient import TestClient

from groundworkers.app import Adapters, GroundworkersApp, Services
from groundworkers.base.errors import GroundworkersError
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.services.source_planning.models import (
    IngestionPlan,
    PreIngestBundle,
    SourceFormat,
)
from groundworkers.services.source_planning.warnings import PlanningWarning
from groundworkers.transports.rest import create_rest_app
from tests.support.stack_config import build_cdm_stack


class FakeMappingService:
    def __init__(self, response: dict | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response or {
            "query": "type 2 diabetes",
            "constraints": {
                "domain": "Condition",
                "vocabulary_id": None,
                "standard_only": False,
                "active_only": True,
                "parent_ids": None,
            },
            "channels": {
                "exact": {
                    "available": True,
                    "results": [],
                    "retrieval_notes": ["case-insensitive exact match"],
                }
            },
            "standardized_candidates": [],
            "candidate_union": [],
            "warnings": [],
        }

    def concept_candidate_bundle(self, query: str, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return self._response


class FakeSourcePlanningService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def plan_source_assisted(self, content: bytes, *, filename: str | None = None, caller_hint: str | None = None):
        self.calls.append(
            {
                "content": content,
                "filename": filename,
                "caller_hint": caller_hint,
            }
        )
        return PreIngestBundle(
            plan=IngestionPlan(
                format_detected=SourceFormat.CSV,
                caller_hint=caller_hint,
                hint_matches=True,
            ),
            warnings=[PlanningWarning(code="TEST_WARNING", message="demo warning")],
            errors=[],
            elapsed_ms=12,
            llm_tier_used=True,
            detected_source_system="redcap",
        )


def _client(
    *,
    mapping=None,
    source_planning=None,
    base_path: str = "/v1",
) -> TestClient:
    config = build_app_config_from_stack(build_cdm_stack())
    app = GroundworkersApp(
        config=config,
        adapters=Adapters(),
        services=Services(
            mapping=mapping,
            source_planning=source_planning,
        ),
    )
    return TestClient(create_rest_app(app, base_path=base_path), raise_server_exceptions=True)


def test_candidate_bundle_endpoint_calls_mapping_service() -> None:
    mapping = FakeMappingService()
    client = _client(mapping=mapping, source_planning=FakeSourcePlanningService())

    response = client.post(
        "/v1/mapping/candidate-bundle",
        json={
            "query": "type 2 diabetes",
            "domain": "Condition",
            "include_embedding": False,
            "per_channel_limit": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["query"] == "type 2 diabetes"
    assert mapping.calls == [
        {
            "query": "type 2 diabetes",
            "domain": "Condition",
            "vocabulary_id": None,
            "standard_only": False,
            "active_only": True,
            "include_synonyms": True,
            "include_normalized": True,
            "include_fulltext": True,
            "include_embedding": False,
            "include_standard_mappings": True,
            "include_hierarchy_context": False,
            "include_relationship_summary": False,
            "parent_ids": None,
            "per_channel_limit": 5,
            "overall_limit": 30,
            "model_name": None,
        }
    ]


def test_assisted_plan_endpoint_decodes_and_serializes_bundle() -> None:
    source_planning = FakeSourcePlanningService()
    client = _client(mapping=FakeMappingService(), source_planning=source_planning)

    response = client.post(
        "/v1/source-planning/assisted-plan",
        json={
            "content": "a,b\n1,2\n",
            "filename": "demo.csv",
            "caller_hint": "data_dictionary",
            "include_intermediate": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["plan"]["format_detected"] == "CSV"
    assert payload["detected_source_system"] == "redcap"
    assert payload["structural_skip_field_types"] == []
    assert payload["packed_value_column_hint"] is None
    assert payload["llm_tier_used"] is True
    assert source_planning.calls == [
        {
            "content": b"a,b\n1,2\n",
            "filename": "demo.csv",
            "caller_hint": "data_dictionary",
        }
    ]


def test_candidate_bundle_endpoint_returns_backend_unavailable_when_service_missing() -> None:
    client = _client(source_planning=FakeSourcePlanningService())

    response = client.post("/v1/mapping/candidate-bundle", json={"query": "type 2 diabetes"})

    assert response.status_code == 503
    assert response.json() == {
        "error": True,
        "code": "BACKEND_UNAVAIL",
        "message": "mapping service is unavailable because the OMOP vocabulary backend is not configured",
    }


def test_value_error_is_exposed_as_invalid_input() -> None:
    class RejectingMappingService:
        def concept_candidate_bundle(self, query: str, **kwargs):
            raise ValueError("query must be a non-empty string")

    client = _client(mapping=RejectingMappingService(), source_planning=FakeSourcePlanningService())

    response = client.post("/v1/mapping/candidate-bundle", json={"query": ""})

    assert response.status_code == 400
    assert response.json() == {
        "error": True,
        "code": "INVALID_INPUT",
        "message": "query must be a non-empty string",
    }


def test_groundworkers_error_code_maps_to_http_status() -> None:
    class FailingSourcePlanningService:
        def plan_source_assisted(self, content: bytes, *, filename: str | None = None, caller_hint: str | None = None):
            raise GroundworkersError("FORMAT_UNRECOGNISED", "unrecognised source format")

    client = _client(mapping=FakeMappingService(), source_planning=FailingSourcePlanningService())

    response = client.post(
        "/v1/source-planning/assisted-plan",
        json={"content": "bogus", "filename": "demo.unknown"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": True,
        "code": "FORMAT_UNRECOGNISED",
        "message": "unrecognised source format",
    }
