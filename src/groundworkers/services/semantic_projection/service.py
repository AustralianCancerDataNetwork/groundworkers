"""SemanticProjectionService — deterministic projection of a grounded concept
into one or more OMOP CDM rows.

Wraps `omop_semantics`'s `OutputDefinitionRuntime`. No LLM call, no database
access: the definition catalogue and domain-based matching are entirely
in-process. Given the same request, this always produces the same result.

Invalid definitions fail at construction time (server startup), not per
request — `OutputDefinitionRuntime` validates row/slot/link/derivation
references when it is built.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from omop_semantics.runtime import (
    OmopSemanticEngine,
    OutputDefinition,
    OutputDefinitionRuntime,
    ProjectedOutputBundle,
    derive_status,
)

from groundworkers.services.semantic_projection.definitions import (
    BUILTIN_DEFINITIONS,
    DefinitionTrigger,
)
from groundworkers.services.semantic_projection.models import (
    ProjectedRowModel,
    ProjectionLinkModel,
    SemanticProjectionRequest,
    SemanticProjectionResult,
    SuppressedRowModel,
)


class SemanticProjectionService:
    def __init__(
        self,
        definitions: Iterable[tuple[OutputDefinition, DefinitionTrigger]] | None = None,
    ) -> None:
        catalogue = tuple(definitions) if definitions is not None else BUILTIN_DEFINITIONS
        engine = OmopSemanticEngine.from_yaml_paths(registry_paths=[], profile_paths=[])
        self._runtime = engine.build_output_definition_runtime([definition for definition, _ in catalogue])
        self._triggers: dict[str, DefinitionTrigger] = {definition.name: trigger for definition, trigger in catalogue}

    @property
    def runtime(self) -> OutputDefinitionRuntime:
        return self._runtime

    def project(self, request: SemanticProjectionRequest) -> SemanticProjectionResult:
        definition_name = request.definition_hint
        if definition_name is not None and definition_name not in self._triggers:
            return SemanticProjectionResult(
                definition_name=None,
                role=None,
                status="no_match",
                audit_notes=[f"Unknown definition_hint '{definition_name}'"],
            )

        if definition_name is None:
            definition_name = self._match_by_domain(request.grounded_domain)
            if definition_name is None:
                return SemanticProjectionResult(
                    definition_name=None,
                    role=None,
                    status="no_match",
                    audit_notes=[self._no_match_reason(request.grounded_domain)],
                )

        context = self._build_context(request)
        bundle = self._runtime.project(definition_name, context)
        return self._to_result(bundle)

    def _match_by_domain(self, domain: str) -> str | None:
        matches = [
            name for name, trigger in self._triggers.items() if not trigger.domains or domain in trigger.domains
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _no_match_reason(self, domain: str) -> str:
        matches = sorted(
            name for name, trigger in self._triggers.items() if not trigger.domains or domain in trigger.domains
        )
        if len(matches) > 1:
            return (
                f"{len(matches)} definitions match domain '{domain}' {matches}; "
                "pass definition_hint to disambiguate"
            )
        return f"No definition matched grounded concept and context (domain '{domain}')"

    @staticmethod
    def _build_context(request: SemanticProjectionRequest) -> dict[str, Any]:
        return {
            "grounded": {
                "concept_id": request.grounded_concept_id,
                "domain": request.grounded_domain,
                "name": request.grounded_concept_name,
            },
            "source": dict(request.context),
        }

    @staticmethod
    def _to_result(bundle: ProjectedOutputBundle) -> SemanticProjectionResult:
        rows = [
            ProjectedRowModel(row_id=row.row_id, table=row.profile.cdm_table, fields=dict(row.fields))
            for row in bundle.rows
        ]
        links = [
            ProjectionLinkModel(
                relationship_type=link.relationship_type,
                source_row=link.source_row,
                target_row=link.target_row,
            )
            for link in bundle.links
        ]
        suppressed_rows = [
            SuppressedRowModel(
                row_id=row.row_id,
                reason=row.reason,
                source_field=row.source_field,
                source_code=row.source_code,
            )
            for row in bundle.suppressed_rows
        ]

        status = derive_status(
            has_rows=bool(bundle.rows),
            has_unresolved=bool(bundle.unresolved_fields),
            has_suppressed=bool(suppressed_rows),
        )

        return SemanticProjectionResult(
            definition_name=bundle.definition_name,
            role=bundle.role,
            status=status,
            rows=rows,
            links=links,
            constraint_checks=list(bundle.constraint_checks),
            unresolved_fields=list(bundle.unresolved_fields),
            suppressed_rows=suppressed_rows,
            audit_notes=list(bundle.audit_notes),
        )
