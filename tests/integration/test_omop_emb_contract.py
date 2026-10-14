from __future__ import annotations

import os

import pytest
from oa_configurator import ResolvedModel, ResolvedProvider
from omop_emb.backends import open_vector_store_reader
from omop_emb.config import MetricType
from omop_emb.interface import EmbeddingReaderInterface

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.bootstrap import build_app_config
from groundworkers.services.graph import GraphService
from groundworkers.services.grounding import ConceptGroundingService
from tests.support.embedding_contract_fixture import (
    MODEL_NAME,
    seed_embedding_contract,
)


class FixedEncoder:
    provider = "ollama"
    model = MODEL_NAME

    def embed_texts(self, texts, *, role, batch_size=None):
        return [[1.0, 0.0, 0.0] if text != "orthogonal query" else [0.0, 1.0, 0.0] for text in texts]

    async def async_embed_texts(self, texts, *, role, batch_size=None):
        return self.embed_texts(texts, role=role, batch_size=batch_size)


@pytest.fixture(scope="module")
def embedding_contract():
    if os.getenv("GROUNDWORKERS_REQUIRE_INTEGRATION") != "1":
        pytest.skip("deterministic pgvector embedding contract runs in required PostgreSQL CI")
    config_path = os.getenv("GROUNDWORKERS_CONFIG_PATH") or os.getenv("GROUNDWORKERS_CONFIG")
    if not config_path:
        raise RuntimeError("required embedding contract needs GROUNDWORKERS_CONFIG_PATH")
    config = build_app_config(config_path=config_path)
    if config.vector_store is None:
        raise AssertionError("required embedding contract has no configured vector store")
    seed_embedding_contract(config)
    adapter = OmopEmbAdapter(
        backend_factory=lambda: open_vector_store_reader(config.vector_store),
        backend_type=config.vector_store.backend_type,
        cdm_session_factory=config.cdm_session_factory,
    )
    adapter._model_backend_factory = lambda: FixedEncoder()
    try:
        yield config, adapter
    finally:
        adapter.close()


@pytest.mark.required_integration
def test_real_reader_returns_seeded_neighbours_and_keeps_batch_alignment(embedding_contract):
    _config, adapter = embedding_contract
    neighbours = adapter.get_neighbours(201826, limit=5, model_name=MODEL_NAME)
    assert [item["concept_id"] for item in neighbours["results"]][:2] == [201827, 201830]
    assert 201826 not in {item["concept_id"] for item in neighbours["results"]}

    record = adapter._resolve_model_record(MODEL_NAME)
    reader = EmbeddingReaderInterface(
        model=record.model_name,
        backend=adapter._get_backend(),
        metric_type=record.metric_type or MetricType.COSINE,
        provider_type=record.provider_type,
    )
    batch = reader.get_nearest_concepts_from_query_texts(
        ["nearest to diabetes", "orthogonal query"],
        model_backend=FixedEncoder(),
        k=1,
        faiss_index_config=record.index_config,
    )
    assert [[match.concept_id for match in row] for row in batch] == [[201826], [201828]]


@pytest.mark.required_integration
def test_real_model_registry_handles_missing_and_ambiguous_selection(embedding_contract):
    _, adapter = embedding_contract
    assert adapter.resolve_model_name(MODEL_NAME) == MODEL_NAME
    with pytest.raises(GroundworkersError) as missing:
        adapter.resolve_model_name("not-registered:v1")
    assert missing.value.code == "NOT_FOUND"
    with pytest.raises(GroundworkersError) as ambiguous:
        adapter.resolve_model_name(None)
    assert ambiguous.value.code == "BACKEND_UNAVAIL"


@pytest.mark.required_integration
def test_real_text_search_applies_domain_vocabulary_standard_and_active_filters(embedding_contract):
    _, adapter = embedding_contract
    result = adapter.search(
        "a deterministic unrelated phrase",
        limit=10,
        domain="Condition",
        vocabulary="SNOMED",
        standard_only=True,
        active_only=True,
        model_name=MODEL_NAME,
    )
    assert {item["concept_id"] for item in result["results"]} == {201826, 201827, 201828}


@pytest.mark.required_integration
def test_graph_grounding_executes_real_embedding_tier_with_seeded_reader(embedding_contract):
    config, _adapter = embedding_contract
    assert config.vector_store is not None
    resolved_model = ResolvedModel(
        name="groundworkers_fixture",
        provider=ResolvedProvider(
            name="fixture_provider", provider="ollama", base_url=None, api_key=None
        ),
        model=MODEL_NAME,
        embedding_dim=3,
        document_prefix=None,
        query_prefix=None,
        embeddings=True,
        tool_use=False,
        structured_output=False,
        extended_thinking=False,
        configuration={},
    )
    graph_adapter = OmopGraphAdapter(
        engine=config.cdm_engine,
        cdm_session_factory=config.cdm_session_factory,
        embedding_backend_factory=lambda: open_vector_store_reader(config.vector_store),
        resolved_embedding_model=resolved_model,
        model_backend_factory=FixedEncoder,
    )
    assert graph_adapter.is_available() is True
    graph = GraphService(graph_adapter)
    assert graph.embedding_resolver_active is True

    result = ConceptGroundingService(graph).ground(
        "a deterministic unrelated phrase",
        limit=5,
        domain="Condition",
        vocabulary_id=None,
    )

    assert result["grounding_explanation"]["matched_tier"] == "EMBEDDING_NEAREST"
    assert result["grounding_explanation"]["used_embedding"] is True
    assert [hit["concept_id"] for hit in result["results"]]
    assert result["results"][0]["concept_id"] == 201826
