from __future__ import annotations

import json
from typing import Any

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer


def register_embedding_resources(server: GroundworkersMCPServer, emb_adapter: OmopEmbAdapter) -> None:
    @server.resource(
        "embedding://models",
        description=(
            "List all locally available embedding models with their backend type, "
            "provider, dimensions, and indexed concept count."
        ),
    )
    def embedding_models() -> str:
        status = emb_adapter.index_status()
        return json.dumps({
            "backend_type": status.get("backend_type"),
            "available": status.get("available", False),
            "models": status.get("models", []),
        })


def register_embedding_tools(server: GroundworkersMCPServer, emb_adapter: OmopEmbAdapter) -> None:
    @server.tool("embedding_index_status")
    def embedding_index_status() -> dict[str, Any]:
        """Returns status of the embedding backend and registered models."""
        try:
            return emb_adapter.index_status()
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("embedding_neighbours")
    def embedding_neighbours(concept_id: int, limit: int = 10, model_name: str | None = None) -> dict[str, Any]:
        """Returns nearest embedding-space neighbours for one OMOP concept."""
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        safe_limit = max(1, min(limit, 50))
        if model_name is not None and not model_name.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "model_name must be a non-empty string"}
        try:
            return emb_adapter.get_neighbours(
                concept_id=concept_id,
                limit=safe_limit,
                model_name=model_name,
            )
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("embedding_search")
    def embedding_search(
        query: str,
        limit: int = 10,
        domain: str | None = None,
        vocabulary: str | None = None,
        standard_only: bool = False,
        active_only: bool = True,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Searches the embedding index by encoding a query string on the fly."""
        if not query.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "query must be a non-empty string"}
        safe_limit = max(1, min(limit, 50))
        try:
            return emb_adapter.search(
                query=query,
                limit=safe_limit,
                domain=domain,
                vocabulary=vocabulary,
                standard_only=standard_only,
                active_only=active_only,
                model_name=model_name,
            )
        except NotImplementedError as exc:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("embedding_encode")
    def embedding_encode(text: str, model_name: str | None = None) -> dict[str, Any]:
        """Encodes free text into one embedding vector using the configured model client."""
        if not text.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "text must be a non-empty string"}
        if model_name is not None and not model_name.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "model_name must be a non-empty string"}
        try:
            return emb_adapter.encode(text=text, model_name=model_name)
        except GroundworkersError as exc:
            return exc.to_dict()
