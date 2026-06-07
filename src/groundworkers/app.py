from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

from omop_emb import EmbeddingBackend, EmbeddingClient

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.adapters.omop_vocab import OmopVocabAdapter
from groundworkers.config import AppConfig
from groundworkers.base.sql import build_engine
from groundworkers.services import MappingService, TextService


@dataclass
class Adapters:
    omop_graph: OmopGraphAdapter | None = None
    omop_vocab: OmopVocabAdapter | None = None
    omop_emb: OmopEmbAdapter | None = None
    llm: LLMAdapter | None = None


@dataclass
class Services:
    mapping: MappingService | None = None
    text: TextService | None = None


@dataclass
class GroundworkersApp:
    config: AppConfig
    adapters: Adapters
    services: Services


def build_adapters(config: AppConfig) -> Adapters:
    adapters = Adapters()

    omop_graph = config.omop_graph
    if omop_graph is not None:
        engine = build_engine(omop_graph.db_url)
        adapters.omop_graph = OmopGraphAdapter(
            engine=engine,
            vocab_schema=omop_graph.vocab_schema,
            emb_model_name=omop_graph.emb_model_name,
            min_fulltext_overlap=omop_graph.min_fulltext_overlap,
        )
        adapters.omop_vocab = OmopVocabAdapter(engine=engine)

    omop_emb = config.omop_emb
    if omop_emb is not None and omop_emb.enabled:
        cdm_engine = adapters.omop_graph.engine if adapters.omop_graph is not None else None

        def build_backend() -> EmbeddingBackend:
            backend_type = omop_emb.backend_type.lower()
            if backend_type == "sqlitevec":
                from omop_emb.backends.sqlitevec import SQLiteVecEmbeddingBackend
                return SQLiteVecEmbeddingBackend.from_path(omop_emb.required_db_path)
            if backend_type == "pgvector":
                from omop_emb.backends.pgvector import PGVectorEmbeddingBackend
                engine = build_engine(omop_emb.required_db_url)
                return PGVectorEmbeddingBackend(emb_engine=engine)
            raise RuntimeError(
                f"Embedding backend_type {omop_emb.backend_type!r} is not supported. "
                "Supported values: sqlitevec, pgvector. "
                "For FAISS-accelerated search, set faiss_cache_dir alongside a supported backend. "
                "FAISS-primary mode (no primary backend) is not yet supported by omop-emb."
            )

        client_factory: Callable[[str], EmbeddingClient] | None = None
        api_credentials = omop_emb.configured_api_credentials
        if api_credentials is not None:
            api_base, api_key = api_credentials

            def build_client(model_name: str) -> EmbeddingClient:
                return EmbeddingClient(
                    model=model_name,
                    api_base=api_base,
                    api_key=api_key,
                )

            client_factory = build_client

        adapters.omop_emb = OmopEmbAdapter(
            backend_factory=build_backend,
            backend_type=omop_emb.backend_type,
            default_model_name=omop_emb.default_model_name,
            client_factory=client_factory,
            cdm_engine=cdm_engine,
            faiss_cache_dir=omop_emb.faiss_cache_dir,
        )

        if client_factory is not None and adapters.omop_graph is not None:
            try:
                record = adapters.omop_emb._resolve_model_record(None)
                adapters.omop_graph.set_embedding_client(
                    client_factory(record.model_name),
                    model_name=record.model_name,
                )
            except Exception as exc:
                _logger.warning(
                    "Embedding client could not be wired into OmopGraphAdapter; "
                    "the embedding tier of concept_ground will be inactive. Reason: %s",
                    exc,
                )

    llm_config = config.llm
    if llm_config is not None and llm_config.enabled:
        from openai import OpenAI
        api_key = llm_config.api_key or "not-needed"
        api_base = llm_config.api_base

        def build_llm_client() -> OpenAI:
            return OpenAI(api_key=api_key, base_url=api_base)

        adapters.llm = LLMAdapter(
            provider=llm_config.provider,
            default_model_name=llm_config.default_model_name,
            client_factory=build_llm_client,
        )

    return adapters


def build_services(adapters: Adapters) -> Services:
    services = Services()
    if adapters.omop_vocab is not None:
        services.mapping = MappingService(
            adapters.omop_vocab,
            graph_adapter=adapters.omop_graph,
            emb_adapter=adapters.omop_emb,
        )
    if adapters.llm is not None:
        services.text = TextService(adapters.llm)
    return services


def build_application(config: AppConfig) -> GroundworkersApp:
    adapters = build_adapters(config)
    app = GroundworkersApp(
        config=config,
        adapters=Adapters(
            omop_graph=adapters.omop_graph,
            omop_vocab=adapters.omop_vocab,
            omop_emb=adapters.omop_emb,
            llm=adapters.llm,
        ),
        services=build_services(adapters),
    )
    return app
