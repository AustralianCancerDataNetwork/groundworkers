from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services.semantic_projection.models import (
    ProjectedRowModel,
    SemanticProjectionRequest,
    SemanticProjectionResult,
)
from groundworkers.tools.semantic_projection_tools import register_semantic_projection_tools


class StubSemanticProjectionService:
    def __init__(self, result: SemanticProjectionResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[SemanticProjectionRequest] = []

    def project(self, request: SemanticProjectionRequest) -> SemanticProjectionResult:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_semantic_project_tool_returns_result_dict() -> None:
    service = StubSemanticProjectionService(
        result=SemanticProjectionResult(
            definition_name="condition_with_status_from_secondary_field",
            role="condition_modifier",
            status="ok",
            rows=[
                ProjectedRowModel(
                    row_id="condition",
                    table="condition_occurrence",
                    fields={"condition_concept_id": 4152280, "condition_status_concept_id": 32902},
                )
            ],
        )
    )
    server = GroundcrewServer("test-server")
    register_semantic_projection_tools(server, service)  # type: ignore[arg-type]

    result = server.call(
        "semantic_project",
        grounded_concept_id=4152280,
        grounded_domain="Condition",
        definition_hint="condition_with_status_from_secondary_field",
        context={"raw_source_fields": {"role_field": "1"}},
    )

    assert result["status"] == "ok"
    assert result["rows"][0]["fields"] == {
        "condition_concept_id": 4152280,
        "condition_status_concept_id": 32902,
    }
    assert len(service.calls) == 1
    assert service.calls[0].grounded_concept_id == 4152280
    assert service.calls[0].context == {"raw_source_fields": {"role_field": "1"}}


def test_semantic_project_tool_defaults_context_to_empty_dict() -> None:
    service = StubSemanticProjectionService(
        result=SemanticProjectionResult(definition_name=None, role=None, status="no_match")
    )
    server = GroundcrewServer("test-server")
    register_semantic_projection_tools(server, service)  # type: ignore[arg-type]

    server.call("semantic_project", grounded_concept_id=1, grounded_domain="Observation")

    assert service.calls[0].context == {}


def test_semantic_project_tool_returns_groundworkers_error_dict() -> None:
    service = StubSemanticProjectionService(error=GroundworkersError("QUERY_ERROR", "definition compile failed"))
    server = GroundcrewServer("test-server")
    register_semantic_projection_tools(server, service)  # type: ignore[arg-type]

    result = server.call("semantic_project", grounded_concept_id=1, grounded_domain="Condition")

    assert result == {
        "error": True,
        "code": "QUERY_ERROR",
        "message": "definition compile failed",
    }


def test_semantic_project_tool_returns_invalid_input_for_bad_request_shape() -> None:
    service = StubSemanticProjectionService(
        result=SemanticProjectionResult(definition_name=None, role=None, status="no_match")
    )
    server = GroundcrewServer("test-server")
    register_semantic_projection_tools(server, service)  # type: ignore[arg-type]

    result = server.call(
        "semantic_project",
        grounded_concept_id="not-an-int-and-not-numeric",
        grounded_domain="Condition",
    )

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert service.calls == []


def test_semantic_project_tool_returns_query_error_for_unexpected_exception() -> None:
    service = StubSemanticProjectionService(error=RuntimeError("boom"))
    server = GroundcrewServer("test-server")
    register_semantic_projection_tools(server, service)  # type: ignore[arg-type]

    result = server.call("semantic_project", grounded_concept_id=1, grounded_domain="Condition")

    assert result["error"] is True
    assert result["code"] == "QUERY_ERROR"
    assert "boom" in result["message"]
