from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.adapters.omop_emb import OmopEmbAdapter


class StubReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get_nearest_concepts_from_query_texts(self, query_texts, embedding_client, *, concept_filter=None, batch_size=None, k=None, faiss_index_config=None):
        self.calls.append(
            (
                "get_nearest_concepts_from_query_texts",
                {
                    "query_texts": query_texts,
                    "embedding_client": embedding_client,
                    "concept_filter": concept_filter,
                    "batch_size": batch_size,
                    "k": k,
                    "faiss_index_config": faiss_index_config,
                },
            )
        )
        return (
            (
                SimpleNamespace(
                    concept_id=111,
                    concept_name="Hypertension",
                    similarity=0.9876543,
                    is_standard=True,
                    is_active=True,
                ),
            ),
        )


class StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def embeddings(self, text, embedding_role):
        self.calls.append(("embeddings", {"text": text, "embedding_role": embedding_role}))
        return np.array([[0.1, 0.2, 0.3]], dtype=float)


def build_adapter() -> OmopEmbAdapter:
    return OmopEmbAdapter(
        backend_factory=lambda: object(),
        backend_type="pgvector",
        default_model_name="demo-model",
        client_factory=lambda model_name: StubClient(),
    )


def test_search_raises_backend_unavail_without_client():
    adapter = OmopEmbAdapter(
        backend_factory=lambda: object(),
        backend_type="pgvector",
        default_model_name="demo-model",
        client_factory=None,
    )

    try:
        adapter.search("hypertension", 5, None, None, False, True, None)
    except GroundworkersError as exc:
        assert exc.code == "BACKEND_UNAVAIL"
        assert "configured model client" in exc.message
    else:
        raise AssertionError("Expected GroundworkersError")


def test_search_returns_serialized_matches(monkeypatch):
    adapter = build_adapter()
    record = SimpleNamespace(model_name="demo-model")
    reader = StubReader()
    client = StubClient()

    monkeypatch.setattr(adapter, "_resolve_model_record", lambda model_name: record)
    monkeypatch.setattr(adapter, "_build_reader", lambda resolved_record: reader)
    monkeypatch.setattr(adapter, "_get_client", lambda model_name: client)
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

    assert result["query_text"] == "hypertension"
    assert result["model_name"] == "demo-model"
    assert result["results"] == [
        {
            "concept_id": 111,
            "concept_name": "Hypertension",
            "similarity": 0.987654,
            "is_standard": True,
            "is_active": True,
        }
    ]
    assert reader.calls == [
        (
            "get_nearest_concepts_from_query_texts",
            {
                "query_texts": ("hypertension",),
                "embedding_client": client,
                "concept_filter": {
                    "limit": 7,
                    "domain": "Condition",
                    "vocabulary": "SNOMED",
                    "standard_only": True,
                    "active_only": True,
                },
                "batch_size": None,
                "k": 7,
                "faiss_index_config": None,
            },
        )
    ]


def test_encode_returns_vector_payload(monkeypatch):
    adapter = build_adapter()
    record = SimpleNamespace(model_name="demo-model")
    client = StubClient()

    monkeypatch.setattr(adapter, "_resolve_model_record", lambda model_name: record)
    monkeypatch.setattr(adapter, "_get_client", lambda model_name: client)

    result = adapter.encode(text="diabetes mellitus", model_name=None)

    assert result == {
        "text": "diabetes mellitus",
        "model_name": "demo-model",
        "dimensions": 3,
        "vector": [0.1, 0.2, 0.3],
    }
    assert client.calls
