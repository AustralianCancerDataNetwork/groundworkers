from __future__ import annotations

import pytest

from groundworkers.services.semantic_projection.models import SemanticProjectionRequest
from groundworkers.services.semantic_projection.service import SemanticProjectionService
from groundworkers.services.semantic_projection.tui_launcher import build_service_project_fn


def test_service_project_fn_matches_direct_service_call() -> None:
    service = SemanticProjectionService()
    project = build_service_project_fn(service)

    via_adapter = project(
        "condition_with_status_from_secondary_field",
        {
            "grounded_concept_id": 4152280,
            "grounded_domain": "Condition",
            "context": {"raw_source_fields": {"role_field": "1"}},
        },
    )

    direct = service.project(
        SemanticProjectionRequest(
            grounded_concept_id=4152280,
            grounded_domain="Condition",
            definition_hint="condition_with_status_from_secondary_field",
            context={"raw_source_fields": {"role_field": "1"}},
        )
    )

    assert via_adapter.model_dump() == direct.model_dump()


def test_service_project_fn_reports_missing_required_context_fields() -> None:
    service = SemanticProjectionService()
    project = build_service_project_fn(service)

    with pytest.raises(ValueError, match="grounded_domain"):
        project(
            "condition_with_status_from_secondary_field",
            {
                "grounded_concept_id": 4152280,
                "context": {"raw_source_fields": {"role_field": "1"}},
            },
        )


def test_service_project_fn_treats_null_context_as_empty_mapping() -> None:
    service = SemanticProjectionService()
    project = build_service_project_fn(service)

    result = project(
        "criteria_gate_condition",
        {
            "grounded_concept_id": 4152280,
            "grounded_domain": "Condition",
            "context": None,
        },
    )

    assert result.definition_name == "criteria_gate_condition"
