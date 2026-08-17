from __future__ import annotations

from types import SimpleNamespace

from omop_emb import MetricType
from sqlalchemy import create_engine

from groundworkers.adapters.omop_graph import OmopGraphAdapter


def test_embedding_resolver_requires_complete_configuration():
    engine = create_engine("sqlite:///:memory:")

    assert OmopGraphAdapter(engine).embedding_resolver_active is False
    assert (
        OmopGraphAdapter(
            engine,
            embedding_backend_factory=lambda: object(),  # type: ignore[arg-type,return-value]
        ).embedding_resolver_active
        is False
    )


def test_knowledge_graph_receives_complete_read_only_embedding_configuration(
    monkeypatch,
):
    captured: dict[str, object] = {}
    embedding_backend = object()
    resolved_model = SimpleNamespace(model="demo-model")

    class FakeEmbeddingConfiguration:
        def __init__(self, **kwargs):
            captured["embedding_configuration_object"] = self
            captured["embedding_configuration"] = kwargs

    class FakeKnowledgeGraph:
        def __init__(self, **kwargs):
            captured["knowledge_graph"] = kwargs

    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraphEmbeddingConfiguration",
        FakeEmbeddingConfiguration,
    )
    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraph", FakeKnowledgeGraph
    )
    adapter = OmopGraphAdapter(
        create_engine("sqlite:///:memory:"),
        embedding_backend_factory=lambda: embedding_backend,  # type: ignore[arg-type,return-value]
        resolved_embedding_model=resolved_model,  # type: ignore[arg-type]
        faiss_cache_dir="faiss-cache",
    )

    knowledge_graph = adapter._get_kg()

    assert isinstance(knowledge_graph, FakeKnowledgeGraph)
    assert captured["embedding_configuration"] == {
        "metric_type": MetricType.COSINE,
        "backend": embedding_backend,
        "resolved_model": resolved_model,
        "write": False,
        "compute_missing_embeddings": False,
        "faiss_cache_dir": "faiss-cache",
    }
    assert captured["knowledge_graph"] == {
        "cdm_engine": adapter.engine,
        "emb_config": captured["embedding_configuration_object"],
    }
    assert adapter.embedding_resolver_active is True


def test_invalid_embedding_configuration_falls_back_to_lexical_graph(monkeypatch):
    captured: dict[str, object] = {}

    class FakeKnowledgeGraph:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraph", FakeKnowledgeGraph
    )

    def fail_backend():
        raise RuntimeError("store unavailable")

    adapter = OmopGraphAdapter(
        create_engine("sqlite:///:memory:"),
        embedding_backend_factory=fail_backend,
        resolved_embedding_model=SimpleNamespace(model="demo-model"),  # type: ignore[arg-type]
    )

    assert isinstance(adapter._get_kg(), FakeKnowledgeGraph)
    assert captured["emb_config"] is None
    assert adapter.embedding_resolver_active is False
    available, detail = adapter.probe()
    assert available is True
    assert detail is not None
    assert "lexical grounding remains available" in detail


def test_wrap_graph_error_does_not_special_case_parentless_not_implemented():
    wrapped = OmopGraphAdapter._wrap_graph_error(
        NotImplementedError("Grounding without parent_ids is not supported."),
        default_code="QUERY_ERROR",
    )

    assert wrapped.code == "QUERY_ERROR"
    assert wrapped.message == "Grounding without parent_ids is not supported."
