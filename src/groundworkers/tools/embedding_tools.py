from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import Field

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer


def register_embedding_resources(
    server: GroundworkersMCPServer, emb_adapter: OmopEmbAdapter
) -> None:
    @server.resource(
        "embedding://models",
        description=(
            "List all locally available embedding models with their backend type, "
            "provider, dimensions, and indexed concept count."
        ),
    )
    def embedding_models() -> str:
        status = emb_adapter.index_status()
        return json.dumps(
            {
                "backend_type": status.get("backend_type"),
                "available": status.get("available", False),
                "models": status.get("models", []),
            }
        )


def register_embedding_tools(
    server: GroundworkersMCPServer, emb_adapter: OmopEmbAdapter
) -> None:
    @server.tool("embedding_index_status")
    def embedding_index_status() -> dict[str, Any]:
        """Returns status of the embedding backend and registered models."""
        try:
            return emb_adapter.index_status()
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("embedding_neighbours")
    def embedding_neighbours(
        concept_id: int,
        limit: int = 10,
        model_name: str | None = None,
        standard_only: bool = True,
        active_only: bool = True,
    ) -> dict[str, Any]:
        """Returns nearest embedding-space neighbours for one OMOP concept.

        The default uses the partial vector index on is_standard AND is_valid. Pass
        standard_only=False to include non-standard neighbours, at the cost of a full scan.
        """
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
        safe_limit = max(1, min(limit, 50))
        if model_name is not None and not model_name.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "model_name must be a non-empty string",
            }
        try:
            return emb_adapter.get_neighbours(
                concept_id=concept_id,
                limit=safe_limit,
                model_name=model_name,
                standard_only=standard_only,
                active_only=active_only,
            )
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("embedding_search")
    async def embedding_search(
        query: Annotated[str, Field(description="Text to search for by meaning.")],
        limit: Annotated[int, Field(description="Maximum results.")] = 10,
        domain: Annotated[
            str | None, Field(description="OMOP domain_id filter.")
        ] = None,
        vocabulary: Annotated[
            str | None, Field(description="Exact vocabulary_id filter.")
        ] = None,
        standard_only: Annotated[
            bool, Field(description="Only standard concepts.")
        ] = False,
        active_only: Annotated[
            bool, Field(description="Exclude retired concepts.")
        ] = True,
        model_name: Annotated[
            str | None,
            Field(description="Embedding model; omit to use the configured default."),
        ] = None,
    ) -> dict[str, Any]:
        """Semantic search by meaning. Use it for abbreviations, paraphrases, misspellings or
        non-English terms that word-based search misses. Similar meaning is not equivalence:
        confirm hits with concept_get and the hierarchy."""
        if not query.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "query must be a non-empty string",
            }
        safe_limit = max(1, min(limit, 50))
        try:
            return await emb_adapter.async_search(
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

    @server.tool("embedding_search_batch")
    async def embedding_search_batch(
        queries: list[str],
        limit: int = 10,
        domain: str | None = None,
        vocabulary: str | None = None,
        standard_only: bool = False,
        active_only: bool = True,
        model_name: str | None = None,
        batch_size: int = 32,
    ) -> dict[str, Any]:
        """Search the embedding index for multiple queries in one batch.

        All queries share the same search filters. Use separate calls when
        domain, vocabulary, or model constraints differ.
        """
        if not queries:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "queries must be non-empty",
            }
        if len(queries) > 256:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "queries must contain at most 256 entries",
            }
        if any(not query.strip() for query in queries):
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "queries must not contain empty strings",
            }
        if batch_size <= 0 or batch_size > 128:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "batch_size must be between 1 and 128",
            }
        safe_limit = max(1, min(limit, 50))
        try:
            return await emb_adapter.async_search_batch(
                queries=queries,
                limit=safe_limit,
                domain=domain,
                vocabulary=vocabulary,
                standard_only=standard_only,
                active_only=active_only,
                model_name=model_name,
                batch_size=batch_size,
            )
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("embedding_encode")
    async def embedding_encode(
        text: str,
        model_name: Annotated[
            str | None,
            Field(
                description="Omit to use the configured live model; no other model is accepted."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Return the raw embedding vector for a text, using the configured embedding model. For
        diagnostics only: embedding_search encodes internally. The vector is large."""
        if not text.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "text must be a non-empty string",
            }
        if model_name is not None and not model_name.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "model_name must be a non-empty string",
            }
        try:
            return await emb_adapter.async_encode(text=text, model_name=model_name)
        except GroundworkersError as exc:
            return exc.to_dict()
