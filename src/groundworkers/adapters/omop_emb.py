from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from omop_emb import (
    EmbeddingBackend,
    EmbeddingConceptFilter,
    EmbeddingModelRecord,
    EmbeddingReaderInterface,
    EmbeddingRole,
    MetricType,
)
from omop_llm import ModelBackend
from sqlalchemy.engine import Engine

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.results import enum_value

logger = logging.getLogger(__name__)


class OmopEmbAdapter:
    """Expose stored-vector and live-query operations through public omop-emb APIs.

    Stored-neighbour operations require only the vector-store backend. Text search
    and encoding additionally require the one model backend resolved for this
    Groundworkers process.
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[[], EmbeddingBackend],
        backend_type: str | None,
        default_model_name: str | None = None,
        model_backend_factory: Callable[[], ModelBackend] | None = None,
        cdm_engine: Engine | None = None,
        faiss_cache_dir: str | None = None,
        configuration_detail: str | None = None,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend_type = backend_type
        self._default_model_name = default_model_name
        self._model_backend_factory = model_backend_factory
        self._cdm_engine = cdm_engine
        self._faiss_cache_dir = faiss_cache_dir
        self._configuration_detail = configuration_detail
        self._backend: EmbeddingBackend | None = None
        self._model_backend: ModelBackend | None = None

    def is_available(self) -> bool:
        """Return whether the store is reachable and has a registered model."""

        return bool(self.index_status()["available"])

    def has_model_backend(self) -> bool:
        """Return whether live text encoding has a configured model backend."""

        return self._model_backend_factory is not None

    def probe_live_query(self) -> tuple[bool, str | None]:
        """Smoke-test the configured model with one real query embedding.

        ``index_status`` intentionally checks only the vector store and model
        registry. This probe verifies the separate capability used by
        ``embedding_search`` and graph embedding resolution.
        """

        try:
            result = self.encode("groundworkers health check", model_name=None)
            vector = result.get("vector")
            if not isinstance(vector, list) or not vector:
                return False, "Embedding provider returned an empty vector."
            return True, None
        except GroundworkersError as exc:
            return False, exc.message
        except Exception as exc:
            return False, f"Embedding query probe failed with {type(exc).__name__}."

    async def async_probe_live_query(self) -> tuple[bool, str | None]:
        """Smoke-test query encoding with the model backend's async client."""

        try:
            result = await self.async_encode("groundworkers health check", model_name=None)
            vector = result.get("vector")
            if not isinstance(vector, list) or not vector:
                return False, "Embedding provider returned an empty vector."
            return True, None
        except GroundworkersError as exc:
            return False, exc.message
        except Exception as exc:
            return False, f"Embedding query probe failed with {type(exc).__name__}."

    def close(self) -> None:
        """Release cached storage and model backends."""

        self._backend = None
        self._model_backend = None

    def resolve_model_name(self, model_name: str | None = None) -> str:
        """Resolve a caller-supplied or configured registered model name."""

        return self._resolve_model_record(model_name).model_name

    def index_status(self) -> dict[str, Any]:
        """Return a secret-safe snapshot of store and registry availability."""

        try:
            backend = self._get_backend()
            records = EmbeddingReaderInterface.list_registered_models(backend=backend)
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
                        "provider": enum_value(record.provider_type),
                        "dimensions": int(record.dimensions),
                        "index_type": enum_value(record.index_type),
                        "concept_count": int(concept_count),
                    }
                )
            status = {
                "available": bool(models),
                "backend_type": self._backend_type
                or self._backend_type_from_backend(backend),
                "models": models,
                "model_backend_configured": self.has_model_backend(),
            }
            if self._configuration_detail is not None:
                status["detail"] = self._configuration_detail
            return status
        except GroundworkersError as exc:
            detail = exc.message
        except Exception as exc:
            # Broad except: converted to a safe status category.
            detail = f"Embedding store inspection failed with {type(exc).__name__}."
        return {
            "available": False,
            "backend_type": self._backend_type,
            "models": [],
            "model_backend_configured": self.has_model_backend(),
            "detail": detail,
        }

    def get_neighbours(
        self,
        concept_id: int,
        limit: int,
        model_name: str | None,
    ) -> dict[str, Any]:
        """Return stored-vector neighbours without calling the model provider."""

        record = self._resolve_model_record(model_name)
        reader = self._build_reader(record)
        try:
            vectors = reader.get_embeddings_by_concept_ids((concept_id,))
        except ValueError as exc:
            raise GroundworkersError(
                "NOT_FOUND",
                f"Concept {concept_id} is not present in the embedding index",
            ) from exc
        if concept_id not in vectors:
            raise GroundworkersError(
                "NOT_FOUND",
                f"Concept {concept_id} is not present in the embedding index",
            )

        vector = np.asarray(vectors[concept_id], dtype=float).reshape(1, -1)
        raw = reader.get_nearest_concepts(
            query_embedding=vector,
            concept_filter=self._build_concept_filter(),
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
        """Encode a query with the configured model and search its registered index."""

        record = self._resolve_model_record(model_name)
        model_backend = self._model_backend_for(record)
        reader = self._build_reader(record)
        concept_filter = self._build_concept_filter(
            domain=domain,
            vocabulary=vocabulary,
            standard_only=standard_only,
            active_only=active_only,
        )
        try:
            raw = reader.get_nearest_concepts_from_query_texts(
                query_texts=(query,),
                model_backend=model_backend,
                concept_filter=concept_filter,
                k=limit,
                faiss_index_config=record.index_config,
            )
        except Exception as exc:
            logger.exception(
                "Embedding search failed for model %r",
                record.model_name,
            )
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "The configured embedding model could not encode the query.",
            ) from exc
        matches = raw[0] if raw else ()
        return {
            "query_text": query,
            "model_name": record.model_name,
            "results": [self._serialise_nearest_match(match) for match in matches],
        }

    def encode(self, text: str, model_name: str | None) -> dict[str, Any]:
        """Encode text with the configured model backend."""

        record = self._resolve_model_record(model_name)
        model_backend = self._model_backend_for(record)
        try:
            array = EmbeddingReaderInterface.generate_embeddings(
                model_backend,
                text,
                role=EmbeddingRole.QUERY,
            )
        except RuntimeError as exc:
            logger.exception(
                "Embedding encode returned an invalid result for model %r",
                record.model_name,
            )
            raise GroundworkersError(
                "QUERY_ERROR",
                "The configured embedding model returned invalid vectors.",
            ) from exc
        except Exception as exc:
            logger.exception(
                "Embedding encode failed for model %r",
                record.model_name,
            )
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "The configured embedding model could not encode the text.",
            ) from exc
        row = array[0]
        return {
            "text": text,
            "model_name": record.model_name,
            "dimensions": int(row.shape[0]),
            "vector": row.tolist(),
        }

    async def async_search(
        self,
        query: str,
        limit: int,
        domain: str | None,
        vocabulary: str | None,
        standard_only: bool,
        active_only: bool,
        model_name: str | None,
    ) -> dict[str, Any]:
        """Encode a query asynchronously, then search the registered index."""

        record = self._resolve_model_record(model_name)
        model_backend = self._model_backend_for(record)
        reader = self._build_reader(record)
        concept_filter = self._build_concept_filter(
            domain=domain,
            vocabulary=vocabulary,
            standard_only=standard_only,
            active_only=active_only,
        )
        try:
            vectors = await model_backend.async_embed_texts(
                [query],
                role=EmbeddingRole.QUERY,
            )
            raw = reader.get_nearest_concepts(
                query_embedding=_embedding_array(vectors, expected_rows=1),
                concept_filter=concept_filter,
                k=limit,
                faiss_index_config=record.index_config,
            )
        except Exception as exc:
            logger.exception(
                "Async embedding search failed for model %r",
                record.model_name,
            )
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "The configured embedding model could not encode the query.",
            ) from exc
        matches = raw[0] if raw else ()
        return {
            "query_text": query,
            "model_name": record.model_name,
            "results": [self._serialise_nearest_match(match) for match in matches],
        }

    async def async_encode(self, text: str, model_name: str | None) -> dict[str, Any]:
        """Encode text with the model backend's native async API."""

        record = self._resolve_model_record(model_name)
        model_backend = self._model_backend_for(record)
        try:
            vectors = await model_backend.async_embed_texts(
                [text],
                role=EmbeddingRole.QUERY,
            )
            array = _embedding_array(vectors, expected_rows=1)
        except RuntimeError as exc:
            logger.exception(
                "Async embedding encode returned an invalid result for model %r",
                record.model_name,
            )
            raise GroundworkersError(
                "QUERY_ERROR",
                "The configured embedding model returned invalid vectors.",
            ) from exc
        except Exception as exc:
            logger.exception(
                "Async embedding encode failed for model %r",
                record.model_name,
            )
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "The configured embedding model could not encode the text.",
            ) from exc
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
                raise GroundworkersError(
                    "BACKEND_UNAVAIL",
                    "The configured embedding store is unavailable.",
                ) from exc
        return self._backend

    def _resolve_model_record(self, model_name: str | None) -> EmbeddingModelRecord:
        backend = self._get_backend()
        requested_name = model_name or self._default_model_name
        records = EmbeddingReaderInterface.list_registered_models(
            backend=backend,
            model_name=requested_name,
        )
        if requested_name is not None:
            if not records:
                raise GroundworkersError(
                    "NOT_FOUND",
                    f"Embedding model {requested_name!r} is not registered",
                )
            return records[0]
        if len(records) == 1:
            return records[0]
        if not records:
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "No embedding models are registered in the configured store",
            )
        raise GroundworkersError(
            "BACKEND_UNAVAIL",
            "Choose a model because the configured store contains multiple models",
        )

    def _build_reader(self, record: EmbeddingModelRecord) -> EmbeddingReaderInterface:
        return EmbeddingReaderInterface(
            model=record.model_name,
            backend=self._get_backend(),
            metric_type=record.metric_type or MetricType.COSINE,
            omop_cdm_engine=self._cdm_engine,
            provider_type=enum_value(record.provider_type) or "ollama",
            faiss_cache_dir=self._faiss_cache_dir,
        )

    def _model_backend_for(self, record: EmbeddingModelRecord) -> ModelBackend:
        if self._model_backend_factory is None:
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "Live query encoding requires a configured embedding model.",
            )
        if self._model_backend is None:
            try:
                self._model_backend = self._model_backend_factory()
            except Exception as exc:
                raise GroundworkersError(
                    "BACKEND_UNAVAIL",
                    "The configured embedding model is unavailable.",
                ) from exc

        configured_provider = self._model_backend.provider
        record_provider = enum_value(record.provider_type)
        if (
            record.model_name != self._model_backend.model
            or record_provider != configured_provider
        ):
            raise GroundworkersError(
                "INVALID_INPUT",
                "Live query encoding can only use the configured embedding model; "
                "use other registered models for stored-vector operations.",
            )
        return self._model_backend

    @staticmethod
    def _build_concept_filter(
        *,
        domain: str | None = None,
        vocabulary: str | None = None,
        standard_only: bool = False,
        active_only: bool = False,
    ) -> EmbeddingConceptFilter:
        return EmbeddingConceptFilter(
            domains=(domain,) if domain else None,
            vocabularies=(vocabulary,) if vocabulary else None,
            require_standard=standard_only,
            require_active=active_only,
        )

    @staticmethod
    def _backend_type_from_backend(backend: EmbeddingBackend) -> str | None:
        return enum_value(getattr(backend, "backend_type", None))

    @staticmethod
    def _serialise_nearest_match(match: Any) -> dict[str, Any]:
        return {
            "concept_id": int(match.concept_id),
            "concept_name": getattr(match, "concept_name", None),
            "similarity": round(float(match.similarity), 6),
            "is_standard": getattr(match, "is_standard", None),
            "is_active": getattr(match, "is_active", None),
        }


def _embedding_array(vectors: object, *, expected_rows: int) -> np.ndarray:
    result = np.asarray(vectors)
    if result.ndim != 2:
        raise RuntimeError(f"Expected 2-D embedding array, got shape {result.shape}")
    if result.shape[0] != expected_rows:
        raise RuntimeError(f"Expected {expected_rows} embeddings, got {result.shape[0]}")
    return result
