"""System-level MCP tools: system_status and system_vocabulary_catalogue.

These tools are always registered regardless of adapter availability, so
callers always get a structured response (never "unknown tool").

system_status — reports availability of every configured adapter.
system_vocabulary_catalogue — returns the full OMOP vocabulary/domain/class
  catalogue from OmopGraphAdapter.  Requires omop_graph to be configured.
"""
from __future__ import annotations

from typing import Any

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer


def register_system_tools(
    server: GroundcrewServer,
    graph_adapter: OmopGraphAdapter | None = None,
    emb_adapter: OmopEmbAdapter | None = None,
    llm_adapter: LLMAdapter | None = None,
) -> None:
    @server.tool("system_status")
    def system_status() -> dict[str, Any]:
        """Returns availability and health of each configured adapter/backend.

        overall is one of:
          "healthy"     — all configured components available
          "degraded"    — at least one configured component unavailable
          "unavailable" — no components available (or none configured)

        components only contains entries for adapters that are configured.
        omop_graph.embedding_resolver_active is true only when an EmbeddingClient
        was successfully wired into the graph adapter at startup — it is independent
        from omop_emb.available and must be checked separately to confirm the
        embedding tier of concept_ground is operational.
        """
        components: dict[str, Any] = {}

        if graph_adapter is not None:
            available, detail = graph_adapter.probe()
            components["omop_graph"] = {
                "available": available,
                "db_connected": available,
                "embedding_resolver_active": graph_adapter.embedding_resolver_active,
                "detail": detail,
            }

        if emb_adapter is not None:
            try:
                status = emb_adapter.index_status()
                components["omop_emb"] = {
                    "available": status["available"],
                    "backend_type": status.get("backend_type"),
                    "model_count": len(status.get("models", [])),
                    "client_configured": emb_adapter.has_client(),
                    "detail": status.get("detail"),
                }
            except Exception as exc:
                components["omop_emb"] = {
                    "available": False,
                    "backend_type": None,
                    "model_count": 0,
                    "client_configured": emb_adapter.has_client(),
                    "detail": repr(exc),
                }

        if llm_adapter is not None:
            try:
                status = llm_adapter.status()
                components["llm"] = {
                    "available": status["available"],
                    "provider": status.get("provider"),
                    "default_model": status.get("default_model"),
                    "structured_output_supported": status.get("structured_output_supported"),
                    "detail": status.get("detail"),
                }
            except Exception as exc:
                components["llm"] = {
                    "available": False,
                    "provider": None,
                    "default_model": None,
                    "structured_output_supported": None,
                    "detail": repr(exc),
                }

        if not components:
            overall = "unavailable"
        elif all(v["available"] for v in components.values()):
            overall = "healthy"
        elif any(v["available"] for v in components.values()):
            overall = "degraded"
        else:
            overall = "unavailable"

        return {"overall": overall, "components": components}

    @server.tool("system_vocabulary_catalogue")
    def system_vocabulary_catalogue() -> dict[str, Any]:
        """Returns all OMOP vocabularies, domains, and concept classes."""
        if graph_adapter is None:
            return {
                "error": True,
                "code": "BACKEND_UNAVAIL",
                "message": "omop_graph adapter is not configured",
            }
        try:
            return graph_adapter.get_vocabulary_catalogue()
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}
