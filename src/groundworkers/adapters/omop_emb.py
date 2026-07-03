from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from sqlalchemy.engine import Engine

from omop_emb import (
    EmbeddingBackend,
    EmbeddingClient,
    EmbeddingConceptFilter,
    EmbeddingModelRecord,
    EmbeddingReaderInterface,
)
from omop_emb.config import MetricType
from omop_emb.embeddings.embedding_client import EmbeddingRole
from omop_emb.interface import list_registered_models

from groundworkers.base.errors import GroundworkersError


class OmopEmbAdapter:
    """Adapter for an omop-emb vector store backend.

    Two operation modes are available depending on configuration:

    - **Index lookup** (`get_neighbours`): uses pre-stored embeddings from the backend; no
      encoding client is required.
    - **Text search** (`search`, `encode`): encodes a query string on-the-fly before searching;
      requires an encoding client (`api_base` / `api_key` in config).

    `has_client()` reflects which mode is available at runtime.
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[[], EmbeddingBackend],
        backend_type: str | None,
        default_model_name: str | None = None,
        client_factory: Callable[[str], EmbeddingClient] | None = None,
        cdm_engine: Engine | None = None,
        faiss_cache_dir: str | None = None,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend_type = backend_type
        self._default_model_name = default_model_name
        self._client_factory = client_factory
        self._cdm_engine = cdm_engine
        self._faiss_cache_dir = faiss_cache_dir
        self._backend: EmbeddingBackend | None = None
        self._clients: dict[str, EmbeddingClient] = {}

    def is_available(self) -> bool:
        """Return True if the backend is reachable and at least one model is registered."""
        return self.index_status()["available"]

    def has_client(self) -> bool:
        """Return True if an encoding client is configured (required for `search` and `encode`)."""
        return self._client_factory is not None

    def close(self) -> None:
        """Release cached backend and client references."""
        self._backend = None
        self._clients.clear()

    def resolve_model_name(self, model_name: str | None = None) -> str:
        """Resolve a caller-supplied or default embedding model name."""
        return self._resolve_model_record(model_name).model_name

    def get_client_for_model(self, model_name: str | None = None) -> EmbeddingClient:
        """Return a configured query-time embedding client for one model."""
        resolved_name = self.resolve_model_name(model_name)
        return self._get_client(resolved_name)

    def index_status(self) -> dict[str, Any]:
        """Return index availability and per-model statistics.

        Always returns a dict — never raises. On backend failure, returns
        ``{"available": False, "backend_type": ..., "models": [], "detail": "<reason>"}``.
        """
        try:
            backend = self._get_backend()
            records = list_registered_models(backend=backend)
            models: list[dict[str, Any]] = []
            for record in records:
                metric_type = record.metric_type or MetricType.COSINE
                concept_count = backend.get_embedding_count(
                    model_name=record.model_name,
                    metric_type=metric_type,
                )
                models.append(
                    {
                        "model_name": record.model_name,
                        "provider": self._enum_value(record.provider_type),
                        "dimensions": int(record.dimensions),
                        "index_type": self._enum_value(record.index_type),
                        "concept_count": int(concept_count),
                    }
                )
            return {
                "available": bool(models),
                "backend_type": self._backend_type or self._backend_type_from_backend(backend),
                "models": models,
            }
        except Exception as exc:
            return {
                "available": False,
                "backend_type": self._backend_type,
                "models": [],
                "detail": repr(exc),
            }

    def get_neighbours(
        self,
        concept_id: int,
        limit: int,
        model_name: str | None,
    ) -> dict[str, Any]:
        """Return the `limit` nearest concepts to `concept_id` using its stored embedding.

        Does not require an encoding client. Raises ``NOT_FOUND`` if the concept has no
        stored embedding in the index.
        """
        record = self._resolve_model_record(model_name)
        reader = self._build_reader(record)
        # omop-emb raises ValueError when a requested concept has no stored embedding;
        # older versions returned a mapping missing that key. Handle both as NOT_FOUND.
        try:
            vectors = reader.get_embeddings_by_concept_ids((concept_id,))
        except ValueError as exc:
            raise GroundworkersError(
                "NOT_FOUND", f"Concept {concept_id} is not present in the embedding index"
            ) from exc
        if concept_id not in vectors:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} is not present in the embedding index")

        vector = np.asarray(vectors[concept_id], dtype=float).reshape(1, -1)
        concept_filter = self._build_concept_filter(limit=limit + 1)
        raw = reader.get_nearest_concepts(
            query_embedding=vector,
            concept_filter=concept_filter,
            k=limit + 1,
            faiss_index_config=record.index_config,
        )
        matches = raw[0] if raw else ()
        results = [
            self._serialise_nearest_match(match)
            for match in matches
            if getattr(match, "concept_id", None) != concept_id
        ][:limit]
        return {
            "query_concept_id": concept_id,
            "model_name": record.model_name,
            "results": results,
        }

    def search(
        self,
        query: str,
        limit: int,
        domain: str | None,
        vocabulary: str | None,
        standard_only: bool,
        active_only: bool,
        model_name: str | None,
    ) -> dict[str, Any]:
        """Encode `query` and return the nearest matching concepts.

        Requires an encoding client (`has_client()` must be True). Results are optionally
        filtered by domain, vocabulary, standard status, and active status.
        """
        if not self.has_client():
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "on-the-fly embedding requires a configured model client",
            )
        record = self._resolve_model_record(model_name)
        reader = self._build_reader(record)
        client = self._get_client(record.model_name)
        concept_filter = self._build_concept_filter(
            limit=limit,
            domain=domain,
            vocabulary=vocabulary,
            standard_only=standard_only,
            active_only=active_only,
        )
        raw = reader.get_nearest_concepts_from_query_texts(
            query_texts=(query,),
            embedding_client=client,
            concept_filter=concept_filter,
            k=limit,
            faiss_index_config=record.index_config,
        )
        matches = raw[0] if raw else ()
        return {
            "query_text": query,
            "model_name": record.model_name,
            "results": [self._serialise_nearest_match(match) for match in matches],
        }

    def encode(self, text: str, model_name: str | None) -> dict[str, Any]:
        """Return the embedding vector for `text`. Requires an encoding client."""
        if not self.has_client():
            raise GroundworkersError("BACKEND_UNAVAIL", "embedding client is not configured")
        record = self._resolve_model_record(model_name)
        client = self._get_client(record.model_name)
        vector = client.embeddings(text, embedding_role=EmbeddingRole.QUERY)
        array = np.asarray(vector, dtype=float)
        if array.ndim != 2 or array.shape[0] != 1:
            raise GroundworkersError("QUERY_ERROR", f"Expected one embedding vector, got shape {array.shape}")
        row = array[0]
        return {
            "text": text,
            "model_name": record.model_name,
            "dimensions": int(row.shape[0]),
            "vector": row.tolist(),
        }

    def _get_backend(self) -> EmbeddingBackend:
        if self._backend is None:
            try:
                self._backend = self._backend_factory()
            except Exception as exc:
                raise GroundworkersError("BACKEND_UNAVAIL", f"Embedding backend is unavailable: {exc}") from exc
        return self._backend

    def _resolve_model_record(self, model_name: str | None) -> EmbeddingModelRecord:
        backend = self._get_backend()
        requested_name = model_name or self._default_model_name
        records = list_registered_models(backend=backend, model_name=requested_name)
        if requested_name is not None:
            if not records:
                raise GroundworkersError("NOT_FOUND", f"Embedding model {requested_name!r} is not registered")
            return records[0]
        if len(records) == 1:
            return records[0]
        if not records:
            raise GroundworkersError("BACKEND_UNAVAIL", "No embedding models are registered in the backend")
        raise GroundworkersError(
            "BACKEND_UNAVAIL",
            "No default embedding model is configured and multiple registered models are available",
        )

    def _build_reader(self, record: EmbeddingModelRecord) -> EmbeddingReaderInterface:
        return EmbeddingReaderInterface(
            model=record.model_name,
            backend=self._get_backend(),
            metric_type=record.metric_type or MetricType.COSINE,
            omop_cdm_engine=self._cdm_engine,
            provider_name_or_type=record.provider_type,
            faiss_cache_dir=self._faiss_cache_dir,
        )

    def _get_client(self, model_name: str) -> EmbeddingClient:
        if self._client_factory is None:
            raise GroundworkersError("BACKEND_UNAVAIL", "embedding client is not configured")
        client = self._clients.get(model_name)
        if client is None:
            try:
                client = self._client_factory(model_name)
            except Exception as exc:
                raise GroundworkersError("BACKEND_UNAVAIL", f"Embedding client is unavailable: {exc}") from exc
            self._clients[model_name] = client
        return client

    def _build_concept_filter(
        self,
        *,
        limit: int,
        domain: str | None = None,
        vocabulary: str | None = None,
        standard_only: bool = False,
        active_only: bool = False,
    ) -> EmbeddingConceptFilter:
        domains = (domain,) if domain else None
        vocabularies = (vocabulary,) if vocabulary else None
        return EmbeddingConceptFilter(
            domains=domains,
            vocabularies=vocabularies,
            require_standard=standard_only,
            require_active=active_only,
            limit=limit,
        )

    def _backend_type_from_backend(self, backend: EmbeddingBackend) -> str | None:
        backend_type = getattr(backend, "backend_type", None)
        return self._enum_value(backend_type)

    def _serialise_nearest_match(self, match: object) -> dict[str, Any]:
        return {
            "concept_id": int(getattr(match, "concept_id")),
            "concept_name": getattr(match, "concept_name", None),
            "similarity": round(float(getattr(match, "similarity")), 6),
            "is_standard": getattr(match, "is_standard", None),
            "is_active": getattr(match, "is_active", None),
        }

    @staticmethod
    def _enum_value(value: object) -> str | None:
        if value is None:
            return None
        return getattr(value, "value", str(value))
