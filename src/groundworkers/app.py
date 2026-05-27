from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from omop_emb import EmbeddingBackend, EmbeddingClient

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.adapters.omop_vocab import OmopVocabAdapter
from groundworkers.config import AppConfig
from groundworkers.base.sql import build_engine
from groundworkers.services import MappingService


@dataclass
class Adapters:
    omop_graph: OmopGraphAdapter | None = None
    omop_vocab: OmopVocabAdapter | None = None
    omop_emb: OmopEmbAdapter | None = None


@dataclass
class Services:
    mapping: MappingService | None = None


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
            raise RuntimeError(f"Unsupported embedding backend type: {omop_emb.backend_type}")

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
            except Exception:
                pass

    return adapters


def build_services(adapters: Adapters) -> Services:
    services = Services()
    if adapters.omop_vocab is not None:
        services.mapping = MappingService(
            adapters.omop_vocab,
            graph_adapter=adapters.omop_graph,
            emb_adapter=adapters.omop_emb,
        )
    return services


def build_application(config: AppConfig) -> GroundworkersApp:
    adapters = build_adapters(config)
    app = GroundworkersApp(
        config=config,
        adapters=Adapters(
            omop_graph=adapters.omop_graph,
            omop_vocab=adapters.omop_vocab,
            omop_emb=adapters.omop_emb,
        ),
        services=build_services(adapters),
    )
    return app
