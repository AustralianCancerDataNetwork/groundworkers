from __future__ import annotations

import pytest

from groundworkers.services.semantic_projection.models import SemanticProjectionRequest
from groundworkers.services.semantic_projection.service import SemanticProjectionService
from groundworkers.services.semantic_projection.tui_launcher import (
    build_service_payload_template,
    build_service_project_fn,
)


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


def test_service_payload_template_uses_explanatory_family_history_example() -> None:
    payload = build_service_payload_template("family_history_condition")

    assert payload == {
        "grounded_concept_id": 378419,
        "grounded_domain": "Condition",
        "grounded_concept_name": "Alzheimer's disease",
        "source_item_id": "mometpr",
        "source_text": "Family history primary diagnosis",
        "context": {"raw_value": "01"},
    }


def test_service_payload_template_uses_bundle_example_for_family_member_history() -> None:
    payload = build_service_payload_template("family_member_history_bundle")

    assert payload == {
        "grounded_concept_id": 378419,
        "grounded_domain": "Condition",
        "grounded_concept_name": "Alzheimer's disease",
        "source_item_id": "mom_bundle",
        "source_text": "Mother family history bundle",
        "context": {
            "raw_source_fields": {
                "relationship_label": "Mother",
                "birth_year": 1940,
                "age_at_death": 82,
                "age_at_onset": 74,
                "method_label": "Records",
                "primary_dx_code": "01",
                "secondary_dx_code": "03",
            }
        },
    }


def test_service_payload_template_falls_back_to_generic_shape_for_unknown_definition() -> None:
    payload = build_service_payload_template("unknown_definition")

    assert payload == {
        "grounded_concept_id": 0,
        "grounded_domain": "",
        "grounded_concept_name": None,
        "source_item_id": None,
        "source_text": None,
        "context": {},
    }
