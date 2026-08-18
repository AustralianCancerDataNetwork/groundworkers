from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from omop_emb import EmbeddingRole, MetricType

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.base.errors import GroundworkersError


def model_record(
    *,
    model_name: str = "demo-model",
    provider_type: str = "ollama",
) -> SimpleNamespace:
    return SimpleNamespace(
        model_name=model_name,
        provider_type=provider_type,
        metric_type=MetricType.COSINE,
        index_config=None,
    )


class StubModelBackend:
    def __init__(
        self,
        *,
        model: str = "demo-model",
        provider: str = "ollama",
        vectors: object = ((0.1, 0.2, 0.3),),
    ) -> None:
        self.model = model
        self.provider = provider
        self.vectors = vectors
        self.calls: list[dict[str, object]] = []

    def embed_texts(self, texts, *, role, batch_size=None):
        self.calls.append({"texts": texts, "role": role, "batch_size": batch_size})
        return self.vectors


class StubReader:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_nearest_concepts_from_query_texts(
        self,
        query_texts,
        model_backend,
        *,
        concept_filter=None,
        batch_size=None,
        k=None,
        faiss_index_config=None,
    ):
        model_backend.embed_texts(
            list(query_texts), role=EmbeddingRole.QUERY, batch_size=batch_size
        )
        self.calls.append(
            {
                "query_texts": query_texts,
                "model_backend": model_backend,
                "concept_filter": concept_filter,
                "batch_size": batch_size,
                "k": k,
                "faiss_index_config": faiss_index_config,
            }
        )
        return ((nearest_match(),),)

    def get_embeddings_by_concept_ids(self, concept_ids):
        self.calls.append({"concept_ids": concept_ids})
        return {111: np.array([0.1, 0.2, 0.3])}

    def get_nearest_concepts(self, query_embedding, **kwargs):
        self.calls.append({"query_embedding": query_embedding, **kwargs})
        return ((nearest_match(concept_id=111), nearest_match(concept_id=222)),)


def nearest_match(*, concept_id: int = 111) -> SimpleNamespace:
    return SimpleNamespace(
        concept_id=concept_id,
        concept_name="Hypertension" if concept_id == 111 else "Essential hypertension",
        similarity=0.9876543,
        is_standard=True,
        is_active=True,
    )


def build_adapter(
    *,
    model_backend: StubModelBackend | None = None,
) -> OmopEmbAdapter:
    return OmopEmbAdapter(
        backend_factory=lambda: object(),  # type: ignore[arg-type,return-value]
        backend_type="pgvector",
        default_model_name="demo-model",
        model_backend_factory=(lambda: model_backend) if model_backend else None,  # type: ignore[arg-type,return-value]
    )


def test_search_requires_model_backend_but_stored_neighbours_do_not(monkeypatch):
    adapter = build_adapter()
    record = model_record()
    reader = StubReader()
    monkeypatch.setattr(adapter, "_resolve_model_record", lambda _: record)
    monkeypatch.setattr(adapter, "_build_reader", lambda _: reader)

    with pytest.raises(GroundworkersError) as caught:
        adapter.search("hypertension", 5, None, None, False, True, None)

    assert caught.value.code == "BACKEND_UNAVAIL"
    assert "configured embedding model" in caught.value.message

    result = adapter.get_neighbours(111, 5, None)
    assert result["query_concept_id"] == 111
    assert [match["concept_id"] for match in result["results"]] == [222]


def test_search_uses_configured_model_backend_and_serialises_matches(monkeypatch):
    backend = StubModelBackend()
    adapter = build_adapter(model_backend=backend)
    record = model_record()
    reader = StubReader()
    monkeypatch.setattr(adapter, "_resolve_model_record", lambda _: record)
    monkeypatch.setattr(adapter, "_build_reader", lambda _: reader)
    monkeypatch.setattr(adapter, "_build_concept_filter", lambda **kwargs: kwargs)

    result = adapter.search(
        query="hypertension",
        limit=7,
        domain="Condition",
        vocabulary="SNOMED",
        standard_only=True,
        active_only=True,
        model_name=None,
    )

    assert result == {
        "query_text": "hypertension",
        "model_name": "demo-model",
        "results": [
            {
                "concept_id": 111,
                "concept_name": "Hypertension",
                "similarity": 0.987654,
                "is_standard": True,
                "is_active": True,
            }
        ],
    }
    assert reader.calls == [
        {
            "query_texts": ("hypertension",),
            "model_backend": backend,
            "concept_filter": {
                "domain": "Condition",
                "vocabulary": "SNOMED",
                "standard_only": True,
                "active_only": True,
            },
            "batch_size": None,
            "k": 7,
            "faiss_index_config": None,
        }
    ]
    assert backend.calls == [
        {
            "texts": ["hypertension"],
            "role": EmbeddingRole.QUERY,
            "batch_size": None,
        }
    ]


def test_encode_uses_query_role_and_returns_vector_payload(monkeypatch):
    backend = StubModelBackend()
    adapter = build_adapter(model_backend=backend)
    monkeypatch.setattr(adapter, "_resolve_model_record", lambda _: model_record())

    result = adapter.encode(text="diabetes mellitus", model_name=None)

    assert result == {
        "text": "diabetes mellitus",
        "model_name": "demo-model",
        "dimensions": 3,
        "vector": [0.1, 0.2, 0.3],
    }
    assert backend.calls == [
        {
            "texts": ["diabetes mellitus"],
            "role": EmbeddingRole.QUERY,
            "batch_size": None,
        }
    ]


def test_encode_rejects_invalid_embedding_shape(monkeypatch):
    backend = StubModelBackend(vectors=(0.1, 0.2, 0.3))
    adapter = build_adapter(model_backend=backend)
    monkeypatch.setattr(adapter, "_resolve_model_record", lambda _: model_record())

    with pytest.raises(GroundworkersError) as caught:
        adapter.encode("hypertension", None)

    assert caught.value.code == "QUERY_ERROR"


def test_live_encoding_rejects_registered_model_that_is_not_configured(monkeypatch):
    backend = StubModelBackend(model="configured-model")
    adapter = build_adapter(model_backend=backend)
    monkeypatch.setattr(adapter, "_resolve_model_record", lambda _: model_record())

    with pytest.raises(GroundworkersError) as caught:
        adapter.encode("hypertension", None)

    assert caught.value.code == "INVALID_INPUT"
    assert backend.calls == []


def test_model_backend_failure_does_not_expose_provider_error(monkeypatch):
    def fail():
        raise RuntimeError("secret-token")

    adapter = OmopEmbAdapter(
        backend_factory=lambda: object(),  # type: ignore[arg-type,return-value]
        backend_type="pgvector",
        default_model_name="demo-model",
        model_backend_factory=fail,
    )
    monkeypatch.setattr(adapter, "_resolve_model_record", lambda _: model_record())

    with pytest.raises(GroundworkersError) as caught:
        adapter.encode("hypertension", None)

    assert caught.value.code == "BACKEND_UNAVAIL"
    assert "secret-token" not in caught.value.message


def test_reader_uses_registry_provider_metric_and_faiss_cache(monkeypatch):
    captured: dict[str, object] = {}

    class CapturingReader:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    adapter = OmopEmbAdapter(
        backend_factory=lambda: "store",  # type: ignore[arg-type,return-value]
        backend_type="pgvector",
        faiss_cache_dir="faiss-cache",
    )
    monkeypatch.setattr(
        "groundworkers.adapters.omop_emb.EmbeddingReaderInterface", CapturingReader
    )

    adapter._build_reader(model_record(provider_type="openai"))

    assert captured == {
        "model": "demo-model",
        "backend": "store",
        "metric_type": MetricType.COSINE,
        "omop_cdm_engine": None,
        "provider_type": "openai",
        "faiss_cache_dir": "faiss-cache",
    }


def test_resolve_model_name_uses_registry_record(monkeypatch):
    adapter = build_adapter()
    monkeypatch.setattr(
        adapter,
        "_resolve_model_record",
        lambda _: model_record(model_name="resolved-model"),
    )

    assert adapter.resolve_model_name() == "resolved-model"
