from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

from omop_emb import EmbeddingBackend, EmbeddingClient

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.config import AppConfig
from sqlalchemy import create_engine
from groundworkers.services import MappingService, TextService, VocabService
from groundworkers.services.source_planning import AssistedColumnRoleClassifier
from groundworkers.services.source_planning import SourcePlanningService


@dataclass
class Adapters:
    cdm: CDMAdapter | None = None
    omop_graph: OmopGraphAdapter | None = None
    omop_emb: OmopEmbAdapter | None = None
    llm: LLMAdapter | None = None


@dataclass
class Services:
    vocab: VocabService | None = None
    mapping: MappingService | None = None
    text: TextService | None = None
    source_planning: SourcePlanningService | None = None


@dataclass
class GroundworkersApp:
    config: AppConfig
    adapters: Adapters
    services: Services


def build_adapters(config: AppConfig) -> Adapters:
    adapters = Adapters()

    omop_graph = config.omop_graph
    if omop_graph is not None:
        engine = create_engine(omop_graph.db_url, future=True)
        adapters.cdm = CDMAdapter(engine)
        adapters.omop_graph = OmopGraphAdapter(
            engine=engine,
            vocab_schema=omop_graph.vocab_schema,
            emb_model_name=omop_graph.emb_model_name,
            min_fulltext_overlap=omop_graph.min_fulltext_overlap,
        )

    omop_emb = config.omop_emb
    if omop_emb is not None and omop_emb.enabled:
        cdm_engine = adapters.cdm.engine if adapters.cdm is not None else None

        def build_backend() -> EmbeddingBackend:
            backend_type = omop_emb.backend_type.lower()
            if backend_type == "sqlitevec":
                from omop_emb.backends.sqlitevec import SQLiteVecEmbeddingBackend
                return SQLiteVecEmbeddingBackend.from_path(omop_emb.required_db_path)
            if backend_type == "pgvector":
                from omop_emb.backends.pgvector import PGVectorEmbeddingBackend
                engine = create_engine(omop_emb.required_db_url, future=True)
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
        api_key = llm_config.api_key
        api_base = llm_config.api_base

        def build_llm_client() -> Any:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The 'openai' package is required for LLM features. "
                    "Install it with: pip install openai"
                ) from exc
            # Pass only what was explicitly configured; when api_key is None the
            # OpenAI SDK falls back to the OPENAI_API_KEY environment variable.
            kwargs: dict[str, Any] = {}
            if api_key is not None:
                kwargs["api_key"] = api_key
            if api_base is not None:
                kwargs["base_url"] = api_base
            return OpenAI(**kwargs)

        adapters.llm = LLMAdapter(
            provider=llm_config.provider,
            default_model_name=llm_config.default_model_name,
            client_factory=build_llm_client,
        )

    return adapters


def build_services(adapters: Adapters) -> Services:
    services = Services()
    assisted_classifier = AssistedColumnRoleClassifier(adapters.llm) if adapters.llm is not None else None
    services.source_planning = SourcePlanningService(assisted_classifier=assisted_classifier)
    if adapters.cdm is not None:
        services.vocab = VocabService(adapters.cdm)
        services.mapping = MappingService(
            services.vocab,
            graph_adapter=adapters.omop_graph,
            emb_adapter=adapters.omop_emb,
        )
    if adapters.llm is not None:
        services.text = TextService(adapters.llm)
    return services


def build_application(config: AppConfig) -> GroundworkersApp:
    adapters = build_adapters(config)
    return GroundworkersApp(
        config=config,
        adapters=adapters,
        services=build_services(adapters),
    )
