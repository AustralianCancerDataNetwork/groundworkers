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
import time
from collections.abc import Awaitable, Callable
from typing import Any

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.config import AppConfig
from groundworkers.services.vocab import VocabService

_HEALTH_PROBE_CACHE_SECONDS = 30.0


def register_system_resources(
    server: GroundworkersMCPServer,
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
            return json.dumps(
                {
                    "error": True,
                    "code": "INTERNAL_ERROR",
                    "message": f"Vocabulary catalogue failed with {type(exc).__name__}.",
                }
            )


def register_system_tools(
    server: GroundworkersMCPServer,
    graph_adapter: OmopGraphAdapter | None = None,
    emb_adapter: OmopEmbAdapter | None = None,
    llm_adapter: LLMAdapter | None = None,
    embedding_configuration_detail: str | None = None,
    vocab_service: VocabService | None = None,
) -> None:
    probe_cache: dict[str, tuple[float, tuple[bool, str | None]]] = {}

    def cached_probe(
        key: str,
        probe: Callable[[], tuple[bool, str | None]],
    ) -> tuple[bool, str | None]:
        now = time.monotonic()
        cached = probe_cache.get(key)
        if cached is not None and now - cached[0] < _HEALTH_PROBE_CACHE_SECONDS:
            return cached[1]
        result = probe()
        probe_cache[key] = (now, result)
        return result

    async def async_cached_probe(
        key: str,
        probe: Callable[[], Awaitable[tuple[bool, str | None]]],
    ) -> tuple[bool, str | None]:
        now = time.monotonic()
        cached = probe_cache.get(key)
        if cached is not None and now - cached[0] < _HEALTH_PROBE_CACHE_SECONDS:
            return cached[1]
        result = await probe()
        probe_cache[key] = (now, result)
        return result

    @server.tool("system_status")
    async def system_status() -> dict[str, Any]:
        """Returns availability and live health of each configured backend.

        overall is one of:
          "healthy"     — all configured components available
          "degraded"    — at least one configured component unavailable
          "unavailable" — no components available (or none configured)

        components only contains entries for configured backends.
        Live full-text and embedding probes are cached briefly to avoid making
        every health request call the model provider or vocabulary database.
        omop_graph.embedding_resolver_active is true only when the graph accepted a
        complete read-only vector-store and resolved-model configuration.
        """
        components: dict[str, Any] = {}
        embedding_live: tuple[bool, str | None] | None = None

        if graph_adapter is not None:
            available, detail = graph_adapter.probe()
            fulltext_live: tuple[bool, str | None] | None = None
            if available and vocab_service is not None:
                fulltext_live = cached_probe("fulltext", vocab_service.probe_fulltext)
                if not fulltext_live[0]:
                    detail = _join_details(detail, fulltext_live[1])
            components["omop_graph"] = {
                "available": available and (fulltext_live is None or fulltext_live[0]),
                "db_connected": available,
                "fulltext_available": (
                    fulltext_live[0] if fulltext_live is not None else None
                ),
                "embedding_resolver_active": graph_adapter.embedding_resolver_active,
                "detail": detail,
            }

        if emb_adapter is not None:
            try:
                status = emb_adapter.index_status()
                live_probe = getattr(emb_adapter, "async_probe_live_query", None)
                if (
                    status["available"]
                    and emb_adapter.has_model_backend()
                    and callable(live_probe)
                ):
                    embedding_live = await async_cached_probe(
                        "embedding",
                        live_probe,
                    )
                store_available = status["available"]
                live_available = (
                    embedding_live[0]
                    if embedding_live is not None
                    else None
                )
                components["omop_emb"] = {
                    "available": store_available and (live_available is None or live_available),
                    "store_available": store_available,
                    "live_query_available": live_available,
                    "backend_type": status.get("backend_type"),
                    "model_count": len(status.get("models", [])),
                    "model_backend_configured": emb_adapter.has_model_backend(),
                    "detail": status.get("detail"),
                }
                if embedding_live is not None and not embedding_live[0]:
                    components["omop_emb"]["detail"] = _join_details(
                        components["omop_emb"]["detail"], embedding_live[1]
                    )
            except Exception as exc:
                components["omop_emb"] = {
                    "available": False,
                    "store_available": False,
                    "live_query_available": None,
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
                "store_available": False,
                "live_query_available": None,
                "detail": embedding_configuration_detail,
            }

        if embedding_live is not None and not embedding_live[0]:
            graph = components.get("omop_graph")
            if graph is not None:
                graph["embedding_resolver_active"] = False

        if llm_adapter is not None:
            try:
                status = await llm_adapter.async_status()
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
                    "detail": f"LLM status failed with {type(exc).__name__}.",
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


def _join_details(first: str | None, second: str | None) -> str | None:
    """Combine redacted health details without producing awkward separators."""

    if first and second:
        return f"{first} {second}"
    return first or second
