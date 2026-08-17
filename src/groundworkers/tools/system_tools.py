"""System-level MCP tools and resources.

Tools are always registered regardless of adapter availability, so
callers always get a structured response (never "unknown tool").

Tools:
  system_status — reports availability of every configured backend.
  system_vocabulary_catalogue — returns the full OMOP vocabulary/domain/class
    catalogue from the omop-graph backend. Requires omop_graph to be configured.

Resources:
  config://active — sanitised view of the active server configuration.
  vocabularies://catalogue — full OMOP vocabulary/domain/concept-class
    catalogue with concept counts. Requires omop_graph to be configured.
"""
from __future__ import annotations

import json
from typing import Any

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.config import AppConfig


def register_system_resources(
    server: GroundcrewServer,
    config: AppConfig,
    graph_adapter: OmopGraphAdapter | None = None,
) -> None:
    @server.resource(
        "config://active",
        description=(
            "Sanitised view of the active server configuration: which adapters are "
            "wired up (omop_graph, omop_emb, llm), their key settings, and the "
            "effective min_fulltext_overlap. API keys are masked."
        ),
    )
    def active_config() -> str:
        return json.dumps(config.describe())

    @server.resource(
        "vocabularies://catalogue",
        description=(
            "Full OMOP vocabulary/domain/concept-class catalogue with concept counts. "
            "Read this before filtering searches by vocabulary or domain. "
            # vocabularies://{vocabulary_id} is reserved for per-vocabulary detail slices.
            "Requires omop_graph to be configured."
        ),
    )
    def vocabularies_catalogue() -> str:
        if graph_adapter is None:
            return json.dumps({
                "error": True,
                "code": "BACKEND_UNAVAIL",
                "message": "omop_graph backend is not configured",
            })
        try:
            return json.dumps(graph_adapter.get_vocabulary_catalogue())
        except GroundworkersError as exc:
            return json.dumps(exc.to_dict())
        except Exception as exc:
            return json.dumps({"error": True, "code": "QUERY_ERROR", "message": repr(exc)})


def register_system_tools(
    server: GroundcrewServer,
    graph_adapter: OmopGraphAdapter | None = None,
    emb_adapter: OmopEmbAdapter | None = None,
    llm_adapter: LLMAdapter | None = None,
    embedding_configuration_detail: str | None = None,
) -> None:
    @server.tool("system_status")
    def system_status() -> dict[str, Any]:
        """Returns availability and health of each configured adapter/backend.

        overall is one of:
          "healthy"     — all configured components available
          "degraded"    — at least one configured component unavailable
          "unavailable" — no components available (or none configured)

        components only contains entries for configured backends.
        omop_graph.embedding_resolver_active is true only when the graph accepted a
        complete read-only vector-store and resolved-model configuration.
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
                    "model_backend_configured": emb_adapter.has_model_backend(),
                    "detail": status.get("detail"),
                }
            except Exception as exc:
                components["omop_emb"] = {
                    "available": False,
                    "backend_type": None,
                    "model_count": 0,
                    "model_backend_configured": emb_adapter.has_model_backend(),
                    "detail": (
                        "Embedding status failed with " f"{type(exc).__name__}."
                    ),
                }
        elif embedding_configuration_detail is not None:
            components["omop_emb"] = {
                "available": False,
                "backend_type": None,
                "model_count": 0,
                "model_backend_configured": True,
                "detail": embedding_configuration_detail,
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
                "message": "omop_graph backend is not configured",
            }
        try:
            return graph_adapter.get_vocabulary_catalogue()
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}
