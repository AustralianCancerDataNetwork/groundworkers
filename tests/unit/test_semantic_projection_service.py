from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from omop_semantics.runtime import ContextFieldRef, OutputDefinition, OutputRowProjection

from groundworkers.services.semantic_projection.definitions import DefinitionTrigger
from groundworkers.services.semantic_projection.models import SemanticProjectionRequest
from groundworkers.services.semantic_projection.service import SemanticProjectionService


def _request(**overrides) -> SemanticProjectionRequest:
    defaults = {
        "grounded_concept_id": 4152280,
        "grounded_domain": "Condition",
        "definition_hint": "condition_with_status_from_secondary_field",
        "context": {"raw_source_fields": {"role_field": "1"}},
    }
    defaults.update(overrides)
    return SemanticProjectionRequest(**defaults)


def test_condition_with_status_projects_primary_role() -> None:
    service = SemanticProjectionService()

    result = service.project(_request())

    assert result.status == "ok"
    assert result.definition_name == "condition_with_status_from_secondary_field"
    assert len(result.rows) == 1
    assert result.rows[0].table == "condition_occurrence"
    assert result.rows[0].fields == {
        "condition_concept_id": 4152280,
        "condition_status_concept_id": 32902,
    }
    assert result.suppressed_rows == []


def test_condition_with_status_maps_contributing_to_secondary_diagnosis() -> None:
    service = SemanticProjectionService()

    result = service.project(
        _request(context={"raw_source_fields": {"role_field": "2"}})
    )

    assert result.rows[0].fields["condition_status_concept_id"] == 32908


def test_condition_with_status_suppresses_non_contributing_role() -> None:
    service = SemanticProjectionService()

    result = service.project(
        _request(context={"raw_source_fields": {"role_field": "3"}})
    )

    assert result.status == "suppressed"
    assert result.rows == []
    assert len(result.suppressed_rows) == 1
    suppressed = result.suppressed_rows[0]
    assert suppressed.row_id == "condition"
    assert suppressed.source_field == "source.raw_source_fields.role_field"
    assert suppressed.source_code == "3"


def test_condition_with_status_missing_secondary_field_is_partial_not_suppressed() -> None:
    service = SemanticProjectionService()

    result = service.project(_request(context={}))

    assert result.status == "partial"
    assert result.rows == []
    assert result.suppressed_rows == []
    assert result.unresolved_fields == [
        {
            "row_id": "condition",
            "missing_fields": ["condition_status_concept_id"],
            "required": True,
        }
    ]


def test_criteria_gate_condition_keeps_row_on_positive_answer() -> None:
    service = SemanticProjectionService()

    result = service.project(
        _request(
            definition_hint="criteria_gate_condition",
            grounded_concept_id=4182210,
            context={"raw_value": "1"},
        )
    )

    assert result.status == "ok"
    assert result.rows[0].fields == {"condition_concept_id": 4182210}


def test_criteria_gate_condition_suppresses_negative_answer() -> None:
    service = SemanticProjectionService()

    result = service.project(
        _request(
            definition_hint="criteria_gate_condition",
            grounded_concept_id=4182210,
            context={"raw_value": "0"},
        )
    )

    assert result.status == "suppressed"
    assert result.rows == []
    assert result.suppressed_rows[0].source_code == "0"


def test_no_hint_reports_no_match_when_domain_is_ambiguous() -> None:
    service = SemanticProjectionService()

    result = service.project(_request(definition_hint=None))

    assert result.status == "no_match"
    assert result.definition_name is None
    assert "definitions match domain 'Condition'" in result.audit_notes[0]


def test_unknown_definition_hint_reports_no_match() -> None:
    service = SemanticProjectionService()

    result = service.project(_request(definition_hint="not_a_real_definition"))

    assert result.status == "no_match"
    assert result.audit_notes == ["Unknown definition_hint 'not_a_real_definition'"]


def test_domain_only_matching_resolves_a_single_unambiguous_candidate() -> None:
    definition = OutputDefinition(
        name="observation_demo",
        role="observation",
        row_projections=(
            OutputRowProjection(
                row_id="observation",
                profile_name="observation_simple",
                field_bindings={"observation_concept_id": ContextFieldRef("grounded.concept_id")},
            ),
        ),
    )
    service = SemanticProjectionService(
        definitions=((definition, DefinitionTrigger(domains=frozenset({"Observation"}))),)
    )

    result = service.project(
        SemanticProjectionRequest(grounded_concept_id=999, grounded_domain="Observation")
    )

    assert result.status == "ok"
    assert result.definition_name == "observation_demo"
