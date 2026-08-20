from __future__ import annotations

import json
from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.source_planning import (
    COLUMN_ROLE_DESCRIPTIONS,
    IngestionStrategy,
    SourcePlanningService,
)
from groundworkers.services.source_planning.canonical_headers import builtin_catalogue
from groundworkers.services.source_planning.serialisation import (
    decode_content,
    serialize_pre_ingest_bundle,
)

_INGESTION_STRATEGY_DESCRIPTIONS: dict[IngestionStrategy, str] = {
    IngestionStrategy.DATA_DICT_IDEAL: "Table has codes plus enough semantic context for direct data-dictionary ingestion.",
    IngestionStrategy.DATA_DICT_SCHEMA: "Table has label/attribute-style structure but lacks explicit code columns.",
    IngestionStrategy.DATA_DICT_PACKED_VALUES: "Table encodes value sets inside cells and should be expanded during ingestion.",
    IngestionStrategy.OWL_ONTOLOGY: "Declared for ontology-style sources; not yet routed by the source planner.",
    IngestionStrategy.FREE_TEXT_EXTRACT: "Declared for free-text sources requiring extraction; not yet routed by the source planner.",
    IngestionStrategy.UNSUPPORTED: "No supported ingestion path was selected for the table.",
}


def register_source_planning_tools(
    server: GroundworkersMCPServer,
    source_planning_service: SourcePlanningService,
) -> None:
    @server.tool("source_plan")
    def source_plan(
        content: str,
        filename: str | None = None,
        caller_hint: str | None = None,
        content_encoding: str | None = "utf-8",
        include_intermediate: bool = False,
    ) -> dict[str, Any]:
        """Plan submitted source content into neutral grounding artifacts.

        Accepts source content as UTF-8 text by default. Pass
        ``content_encoding="base64"`` when submitting binary formats such as
        XLSX, PDF, or DOCX. The result always includes the final
        ``IngestionPlan`` and may optionally include intermediate raw,
        normalized, and annotated tables for inspection.
        """

        try:
            raw_content = decode_content(content, content_encoding)
            bundle = source_planning_service.plan_source(
                raw_content,
                filename=filename,
                caller_hint=caller_hint,
            )
            return serialize_pre_ingest_bundle(bundle, include_intermediate=include_intermediate)
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("source_plan_assisted")
    def source_plan_assisted(
        content: str,
        filename: str | None = None,
        caller_hint: str | None = None,
        content_encoding: str | None = "utf-8",
        include_intermediate: bool = False,
    ) -> dict[str, Any]:
        """Plan submitted source content with explicit LLM-assisted classification.

        This tool is the explicit second-step source-planning path for cases
        where deterministic classification was not strong enough. It preserves
        fallback provenance in the returned planning artifacts. When no LLM
        adapter is configured, the tool returns ``BACKEND_UNAVAIL``.
        """

        try:
            raw_content = decode_content(content, content_encoding)
            bundle = source_planning_service.plan_source_assisted(
                raw_content,
                filename=filename,
                caller_hint=caller_hint,
            )
            return serialize_pre_ingest_bundle(bundle, include_intermediate=include_intermediate)
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()


def register_source_planning_resources(server: GroundworkersMCPServer) -> None:
    @server.resource(
        "source-planning://canonical-headers",
        description=(
            "Authoritative Tier A canonical header catalogue used for deterministic "
            "source-planning classification."
        ),
    )
    def source_planning_canonical_headers() -> str:
        return json.dumps(builtin_catalogue())

    @server.resource(
        "source-planning://column-roles",
        description=(
            "ColumnRole values and descriptions for interpreting annotated "
            "source-planning outputs."
        ),
    )
    def source_planning_column_roles() -> str:
        return json.dumps(
            {
                role.value: {
                    "description": description,
                }
                for role, description in COLUMN_ROLE_DESCRIPTIONS.items()
            }
        )

    @server.resource(
        "source-planning://ingestion-strategies",
        description=(
            "IngestionStrategy values and descriptions for interpreting "
            "source-planning route decisions."
        ),
    )
    def source_planning_ingestion_strategies() -> str:
        return json.dumps(
            {
                strategy.value: {
                    "description": description,
                }
                for strategy, description in _INGESTION_STRATEGY_DESCRIPTIONS.items()
            }
        )
