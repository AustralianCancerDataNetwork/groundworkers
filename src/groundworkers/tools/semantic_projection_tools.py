from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.semantic_projection.models import (
    SemanticProjectionRequest,
    SemanticProjectionResult,
)


class SemanticProjector(Protocol):
    """The projection surface this tool needs — `SemanticProjectionService`
    satisfies it structurally, as does any test double with the same method."""

    def project(self, request: SemanticProjectionRequest) -> SemanticProjectionResult: ...


def register_semantic_projection_tools(server: GroundworkersMCPServer, service: SemanticProjector) -> None:
    @server.tool("semantic_project")
    def semantic_project(
        grounded_concept_id: int,
        grounded_domain: str,
        grounded_concept_name: str | None = None,
        source_text: str | None = None,
        source_item_id: str | None = None,
        definition_hint: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deterministically project a grounded concept into one or more CDM rows.

        Given a concept already grounded by other tools (e.g. `concept_ground`),
        selects a registered output definition and returns the CDM rows, links,
        and any suppressed rows it produces. No LLM call — the same input always
        produces the same output.

        *context* carries whatever the definition needs beyond the grounded
        concept itself:
        - `raw_value` — the grounded field's own raw source code, consulted when
          the field's own value decides whether the row exists at all (e.g. a
          "meets criteria for X" field, where a negative answer suppresses it).
        - `raw_source_fields` — a mapping of well-known slot name to raw value,
          for definitions that resolve a row's slot from a *different* source
          field (e.g. a diagnosis's Primary/Contributing/Non-contributing role,
          collected in a sibling field). Populate the key the definition
          documents in its notes, not the field's actual name in your source
          data.

        Pass *definition_hint* to select a definition explicitly. Required
        whenever more than one registered definition could apply to the same
        *grounded_domain* — domain alone does not disambiguate them, and this
        tool will not guess.

        Returns `status` of `ok`, `partial`, `suppressed`, or `no_match`. Rows
        dropped by a derivation rule or special-value policy are never silently
        absent — they appear in `suppressed_rows` with the reason.
        """
        try:
            request = SemanticProjectionRequest(
                grounded_concept_id=grounded_concept_id,
                grounded_domain=grounded_domain,
                grounded_concept_name=grounded_concept_name,
                source_text=source_text,
                source_item_id=source_item_id,
                definition_hint=definition_hint,
                context=context or {},
            )
        except ValidationError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}

        try:
            result = service.project(request)
            return result.model_dump()
        except GroundworkersError as exc:
            return exc.to_dict()
