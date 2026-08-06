from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from groundworkers.services.semantic_projection.models import (
    SemanticProjectionRequest,
    SemanticProjectionResult,
)
from groundworkers.services.semantic_projection.service import SemanticProjectionService

ProjectCallable = Callable[[str, Mapping[str, Any]], SemanticProjectionResult]
_REQUIRED_CONTEXT_FIELDS = ("grounded_concept_id", "grounded_domain")
_DEFAULT_SERVICE_PAYLOAD = {
    "grounded_concept_id": 0,
    "grounded_domain": "",
    "grounded_concept_name": None,
    "source_item_id": None,
    "source_text": None,
    "context": {},
}
_EXAMPLE_SERVICE_PAYLOADS: dict[str, dict[str, Any]] = {
    "condition_with_status_from_secondary_field": {
        "grounded_concept_id": 4152280,
        "grounded_domain": "Condition",
        "grounded_concept_name": "Major depressive disorder",
        "source_item_id": "majdepdx",
        "source_text": "Major depressive disorder",
        "context": {"raw_source_fields": {"role_field": "1"}},
    },
    "family_history_condition": {
        "grounded_concept_id": 378419,
        "grounded_domain": "Condition",
        "grounded_concept_name": "Alzheimer's disease",
        "source_item_id": "mometpr",
        "source_text": "Family history primary diagnosis",
        "context": {"raw_value": "01"},
    },
    "family_member_history_bundle": {
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
    },
    "criteria_gate_condition": {
        "grounded_concept_id": 4182210,
        "grounded_domain": "Condition",
        "grounded_concept_name": "Dementia",
        "source_item_id": "demented",
        "source_text": "Meets criteria for dementia",
        "context": {"raw_value": "1"},
    },
    "yes_no_observation": {
        "grounded_concept_id": 42710016,
        "grounded_domain": "Observation",
        "grounded_concept_name": "Normal cognition",
        "source_item_id": "normcog",
        "source_text": "Unimpaired cognition and behavior",
        "context": {"raw_value": "1"},
    },
    "measurement_numeric_with_unit_from_context": {
        "grounded_concept_id": 3036277,
        "grounded_domain": "Measurement",
        "grounded_concept_name": "Body height",
        "source_item_id": "height",
        "source_text": "Body height",
        "context": {
            "numeric_value": 172.4,
            "raw_source_fields": {"unit_code": "cm"},
        },
    },
}


def build_service_project_fn(service: SemanticProjectionService) -> ProjectCallable:
    def project(definition_hint: str, raw: Mapping[str, Any]) -> SemanticProjectionResult:
        missing = [field for field in _REQUIRED_CONTEXT_FIELDS if field not in raw]
        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"Missing required context field(s): {fields}")
        request = SemanticProjectionRequest(
            grounded_concept_id=raw["grounded_concept_id"],
            grounded_domain=raw["grounded_domain"],
            grounded_concept_name=raw.get("grounded_concept_name"),
            source_text=raw.get("source_text"),
            source_item_id=raw.get("source_item_id"),
            definition_hint=definition_hint,
            context=dict(raw.get("context") or {}),
        )
        return service.project(request)

    return project


def project_from_tui_template(
    service: SemanticProjectionService,
    definition_name: str,
    raw: Mapping[str, Any],
) -> SemanticProjectionResult:
    """Project one TUI sample payload through the existing service adapter."""

    return build_service_project_fn(service)(definition_name, raw)


def build_service_payload_template(definition_name: str) -> dict[str, Any]:
    """Return a TUI-friendly starter payload for one shipped definition."""

    template = deepcopy(_DEFAULT_SERVICE_PAYLOAD)
    template.update(deepcopy(_EXAMPLE_SERVICE_PAYLOADS.get(definition_name, {})))
    return template


class GroundworkersProjectionExplorer:
    """Factory for a custom TUI app with clearer starter payloads."""

    @staticmethod
    def create(service: SemanticProjectionService):
        from omop_semantics.runtime.tui import OutputDefinitionExplorer

        class _GroundworkersProjectionExplorer(OutputDefinitionExplorer):
            def _context_template(self, definition_name: str) -> dict[str, Any]:
                if self._service_mode:
                    return build_service_payload_template(definition_name)
                return super()._context_template(definition_name)

        return _GroundworkersProjectionExplorer(
            service.runtime,
            project_fn=build_service_project_fn(service),
        )


def run_semantic_projection_tui(service: SemanticProjectionService | None = None) -> None:
    try:
        from omop_semantics.runtime.tui import OutputDefinitionExplorer
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "Semantic projection TUI requires the omop-semantics TUI dependencies. "
            "Install groundworkers with its 'tui' extra or ensure textual is installed."
        ) from exc
    _ = OutputDefinitionExplorer

    active_service = service or SemanticProjectionService()
    GroundworkersProjectionExplorer.create(active_service).run()
