from __future__ import annotations

from typing import Any, Callable, Mapping

from groundworkers.services.semantic_projection.models import (
    SemanticProjectionRequest,
    SemanticProjectionResult,
)
from groundworkers.services.semantic_projection.service import SemanticProjectionService

ProjectCallable = Callable[[str, Mapping[str, Any]], SemanticProjectionResult]
_REQUIRED_CONTEXT_FIELDS = ("grounded_concept_id", "grounded_domain")


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
            context=dict(raw.get("context", {})),
        )
        return service.project(request)

    return project


def run_semantic_projection_tui(service: SemanticProjectionService | None = None) -> None:
    try:
        from omop_semantics.runtime.tui import run_tui
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "Semantic projection TUI requires the omop-semantics TUI dependencies. "
            "Install groundworkers with its 'tui' extra or ensure textual is installed."
        ) from exc

    active_service = service or SemanticProjectionService()
    run_tui(active_service.runtime, project_fn=build_service_project_fn(active_service))
