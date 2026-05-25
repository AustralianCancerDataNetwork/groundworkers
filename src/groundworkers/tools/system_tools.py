"""System-level MCP tools: system_status and system_vocabulary_catalogue.

These tools are always registered regardless of adapter availability, so
callers always get a structured response (never "unknown tool").

system_status — reports availability of every configured adapter.
system_vocabulary_catalogue — returns the full OMOP vocabulary/domain/class
  catalogue from OmopGraphAdapter.  Requires omop_graph to be configured.
"""
from __future__ import annotations

from typing import Any

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer


def register_system_tools(
    server: GroundcrewServer,
    graph_adapter: OmopGraphAdapter | None = None,
    emb_adapter: OmopEmbAdapter | None = None,
) -> None:
    @server.tool("system_status")
    def system_status() -> dict[str, Any]:
        """Returns availability of each configured adapter/backend."""
        adapters: dict[str, Any] = {}

        if graph_adapter is not None:
            try:
                adapters["omop_graph"] = {"available": graph_adapter.is_available()}
            except Exception as exc:
                adapters["omop_graph"] = {"available": False, "reason": repr(exc)}
        else:
            adapters["omop_graph"] = {"available": False, "reason": "not configured"}

        if emb_adapter is not None:
            try:
                status = emb_adapter.index_status()
                adapters["omop_emb"] = {
                    "available": status["available"],
                    "models": status.get("models", []),
                }
            except Exception as exc:
                adapters["omop_emb"] = {"available": False, "reason": repr(exc)}
        else:
            adapters["omop_emb"] = {"available": False, "reason": "not configured"}

        overall = any(v.get("available") for v in adapters.values())
        return {"available": overall, "adapters": adapters}

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
