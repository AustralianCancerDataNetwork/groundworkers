from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, Resolver
from omop_alchemy.config import create_cdm_engines
from omop_alchemy.cross_database import cdm_sessionmaker
from omop_emb import MetricType
from omop_graph import KnowledgeGraph
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.domain_names import DomainNameResolver
from tests.support.stack_config import build_cdm_stack


def test_async_query_encoding_uses_native_model_api_repeatedly():
    class AsyncOnlyBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed_texts(self, *args, **kwargs):
            raise AssertionError("sync embedding API must not be called")

        async def async_embed_texts(self, texts, *, role):
            self.calls.extend(texts)
            return [[0.1, 0.2, 0.3] for _ in texts]

    backend = AsyncOnlyBackend()
    adapter = OmopGraphAdapter(
        _engine_with_graph_tables(),
        model_backend_factory=lambda: backend,  # type: ignore[arg-type,return-value]
    )

    async def invoke_twice():
        return (
            await adapter._async_encode_query("first"),
            await adapter._async_encode_query("second"),
        )

    first, second = asyncio.run(invoke_twice())

    assert first.shape == (1, 3)
    assert second.shape == (1, 3)
    assert backend.calls == ["first", "second"]


def test_embedding_resolver_requires_complete_configuration():
    engine = _engine_with_graph_tables()

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
        def __init__(self, cdm_session_factory, *, emb_config):
            captured["knowledge_graph"] = {
                "cdm_session_factory": cdm_session_factory,
                "emb_config": emb_config,
            }

    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraphEmbeddingConfiguration",
        FakeEmbeddingConfiguration,
    )
    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraph", FakeKnowledgeGraph
    )
    adapter = OmopGraphAdapter(
        _engine_with_graph_tables(),
        embedding_backend_factory=lambda: embedding_backend,  # type: ignore[arg-type,return-value]
        resolved_embedding_model=resolved_model,  # type: ignore[arg-type]
        model_backend_factory=lambda: object(),  # type: ignore[arg-type,return-value]
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
        "cdm_session_factory": adapter._cdm_session_factory,
        "emb_config": captured["embedding_configuration_object"],
    }
    assert adapter.embedding_resolver_active is True


def test_invalid_embedding_configuration_falls_back_to_lexical_graph(monkeypatch):
    captured: dict[str, object] = {}

    class FakeKnowledgeGraph:
        def __init__(self, cdm_session_factory, *, emb_config):
            captured.update(cdm_session_factory=cdm_session_factory, emb_config=emb_config)

    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraph", FakeKnowledgeGraph
    )

    def fail_backend():
        raise RuntimeError("store unavailable")

    adapter = OmopGraphAdapter(
        _engine_with_graph_tables(),
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


def test_cdm_only_configuration_builds_a_lexical_graph(monkeypatch):
    captured: dict[str, object] = {}

    class FakeKnowledgeGraph:
        def __init__(self, cdm_session_factory, *, emb_config):
            captured.update(cdm_session_factory=cdm_session_factory, emb_config=emb_config)

    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraph", FakeKnowledgeGraph
    )
    adapter = OmopGraphAdapter(_engine_with_graph_tables())

    assert isinstance(adapter._get_kg(), FakeKnowledgeGraph)
    assert captured["emb_config"] is None
    assert adapter.embedding_resolver_active is False
    assert adapter.probe() == (True, None)


def test_graph_rejecting_the_embedding_configuration_keeps_lexical_grounding(monkeypatch):
    """The KG itself can reject an otherwise-constructible embedding configuration.

    That must not fail the whole backend or leave embeddings reported as active.
    """
    attempts: list[object] = []

    class FakeKnowledgeGraph:
        def __init__(self, cdm_session_factory, *, emb_config):
            attempts.append(emb_config)
            if emb_config is not None:
                raise RuntimeError("embedding store has no vectors for demo-model")

    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraph", FakeKnowledgeGraph
    )
    adapter = OmopGraphAdapter(
        _engine_with_graph_tables(),
        embedding_backend_factory=lambda: object(),  # type: ignore[arg-type,return-value]
        resolved_embedding_model=SimpleNamespace(model="demo-model"),  # type: ignore[arg-type]
    )

    assert isinstance(adapter._get_kg(), FakeKnowledgeGraph)
    assert len(attempts) == 2
    assert attempts[1] is None
    assert adapter.embedding_resolver_active is False
    available, detail = adapter.probe()
    assert available is True
    assert detail is not None
    assert "lexical grounding remains available" in detail


def test_embedding_failure_detail_does_not_leak_provider_or_database_secrets():
    detail = OmopGraphAdapter._embedding_failure_detail(
        RuntimeError(
            "connection to postgresql://airflow:sekrit@localhost:15432/db failed; "
            "api_key=sk-abc123"
        )
    )

    assert "sekrit" not in detail
    assert "sk-abc123" not in detail
    assert "RuntimeError" in detail


def test_backend_failure_without_embedding_configuration_still_raises(monkeypatch):
    class FakeKnowledgeGraph:
        def __init__(self, cdm_session_factory, *, emb_config):
            raise RuntimeError("graph unavailable")

    monkeypatch.setattr(
        "groundworkers.adapters.omop_graph.KnowledgeGraph", FakeKnowledgeGraph
    )
    adapter = OmopGraphAdapter(_engine_with_graph_tables())

    available, detail = adapter.probe()
    assert available is False
    assert detail == "omop-graph operation failed with RuntimeError."
    assert adapter.embedding_resolver_active is False


def test_real_knowledge_graph_constructs_with_sqlite_adapter():
    engine = create_engine(
        "sqlite:///:memory:"
    ).execution_options(schema_translate_map={"primary": None, "vocab": None, "results": None})
    from omop_alchemy.cdm.model.vocabulary import Concept
    from omop_graph.extensions.omop_alchemy import RelationshipMapping

    Concept.__table__.create(engine)
    RelationshipMapping.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
                RelationshipMapping.__table__.insert(),
                {
                    "relationship_id": "Maps to",
                    "predicate_kind": "Hierarchy",
                    "predicate_subkind": "mapping",
            },
        )
    adapter = OmopGraphAdapter(engine)

    graph = adapter._get_kg()

    assert isinstance(graph, KnowledgeGraph)


def test_real_knowledge_graph_constructs_with_split_sqlite_databases(
    tmp_path: Path,
) -> None:
    stack = build_cdm_stack(sqlite_path=str(tmp_path / "primary.db"))
    stack.connections["vocab"] = ConnectionConfig(
        dialect="sqlite", database_name=str(tmp_path / "vocabulary.db")
    )
    stack.databases["cdm_db"] = CDMDatabaseConfig(
        connection="cdm_main", vocab_connection="vocab"
    )
    resolved = Resolver(stack).resolve_database("cdm_db")
    primary_engine, vocab_engine = create_cdm_engines(
        resolved, register_claims=False
    )
    from omop_alchemy.cdm.model.vocabulary import (
        Concept,
        Concept_Class,
        Domain,
        Vocabulary,
    )
    from omop_graph.extensions.omop_alchemy import RelationshipMapping

    RelationshipMapping.__table__.create(primary_engine)
    for model in (Concept_Class, Domain, Vocabulary, Concept):
        model.__table__.create(vocab_engine)
    with primary_engine.begin() as connection:
        connection.execute(
            RelationshipMapping.__table__.insert(),
            {
                "relationship_id": "Maps to",
                "predicate_kind": "Hierarchy",
                "predicate_subkind": "mapping",
            },
        )
    session_factory = cdm_sessionmaker(
        resolved, primary=primary_engine, vocab=vocab_engine
    )

    try:
        available, detail = OmopGraphAdapter(primary_engine).probe()
        assert available is False
        assert detail is not None and "vocabulary on its own database" in detail
        with pytest.raises(RuntimeError, match="vocabulary on its own database"):
            DomainNameResolver(primary_engine).known_domains()
        graph = OmopGraphAdapter(session_factory)._get_kg()
        assert isinstance(graph, KnowledgeGraph)
    finally:
        primary_engine.dispose()
        if vocab_engine is not primary_engine:
            vocab_engine.dispose()


def test_match_kind_names_are_keyed_off_the_upstream_enum():
    from omop_graph.graph.nodes import LabelMatchKind

    assert OmopGraphAdapter._label_match_kind_name(LabelMatchKind.EXACT) == "EXACT"
    assert OmopGraphAdapter._label_match_kind_name(LabelMatchKind.FTS) == "FULLTEXT"
    assert OmopGraphAdapter._label_match_kind_name(LabelMatchKind.PARTIAL) == "PARTIAL"
    assert (
        OmopGraphAdapter._label_match_kind_name(LabelMatchKind.EMBEDDING)
        == "EMBEDDING_NEAREST"
    )
    # Every upstream member is mapped, so a new tier cannot silently stringify.
    assert set(OmopGraphAdapter._MATCH_KIND_NAMES) == set(LabelMatchKind)


def test_wrap_graph_error_does_not_special_case_parentless_not_implemented():
    wrapped = OmopGraphAdapter._wrap_graph_error(
        NotImplementedError("Grounding without parent_ids is not supported."),
        default_code="QUERY_ERROR",
    )

    assert wrapped.code == "QUERY_ERROR"
    assert wrapped.message == (
        "omop-graph operation failed with NotImplementedError."
    )


def test_wrap_graph_error_classifies_primary_database_outage_before_setup_text():
    outage = OperationalError("select", {}, ConnectionError("server unavailable"))
    wrapped = OmopGraphAdapter._wrap_graph_error(outage, default_code="QUERY_ERROR")

    assert wrapped.code == "BACKEND_UNAVAIL"
    assert "database is unavailable" in wrapped.message


def _engine_with_graph_tables():
    from omop_alchemy.cdm.model.vocabulary import Concept
    from omop_graph.extensions.omop_alchemy import RelationshipMapping

    engine = create_engine("sqlite:///:memory:").execution_options(
        schema_translate_map={"primary": None, "vocab": None, "results": None}
    )
    Concept.__table__.create(engine)
    RelationshipMapping.__table__.create(engine)
    return engine
