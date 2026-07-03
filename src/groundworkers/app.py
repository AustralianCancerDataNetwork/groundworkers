from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from omop_emb import EmbeddingBackend, EmbeddingClient
from omop_emb.config import BackendType, ProviderType

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.config import AppConfig
from groundworkers.services import ConceptGroundingService, DomainService, GraphService, MappingService, TextService, VocabService
from groundworkers.services.source_planning import AssistedColumnRoleClassifier
from groundworkers.services.source_planning import SourcePlanningService

_logger = logging.getLogger(__name__)


@dataclass
class Adapters:
    cdm: CDMAdapter | None = None
    omop_graph: OmopGraphAdapter | None = None
    omop_emb: OmopEmbAdapter | None = None
    llm: LLMAdapter | None = None


@dataclass
class Services:
    vocab: VocabService | None = None
    graph: GraphService | None = None
    grounding: ConceptGroundingService | None = None
    mapping: MappingService | None = None
    text: TextService | None = None
    source_planning: SourcePlanningService | None = None
    domain: DomainService | None = None


@dataclass
class GroundworkersApp:
    config: AppConfig
    adapters: Adapters
    services: Services


def build_adapters(config: AppConfig) -> Adapters:
    adapters = Adapters()

    if config.cdm_engine is not None:
        adapters.cdm = CDMAdapter(config.cdm_engine)

    omop_graph = config.omop_graph
    if omop_graph is not None and config.cdm_engine is not None and config.cdm_resource_name is not None:
        resolved_resource = config.resolver.resolve_resource(config.cdm_resource_name)
        adapters.omop_graph = OmopGraphAdapter(
            engine=config.cdm_engine,
            vocab_schema=resolved_resource.vocab_schema,
            emb_model_name=config.effective_embedding_model_name,
        )

    omop_emb = config.omop_emb
    if omop_emb is not None:
        cdm_engine = adapters.cdm.engine if adapters.cdm is not None else None

        # Reject an unsupported backend eagerly at build time with an actionable
        # message, rather than lazily as an opaque BACKEND_UNAVAIL on first query.
        # FAISS is a common point of confusion: it is a query-time cache
        # accelerator (omop_emb.faiss_cache_dir + the 'embedding-faiss' extra),
        # not a standalone backend, so 'faiss' is not a valid backend value.
        try:
            BackendType(omop_emb.backend)
        except ValueError:
            supported = ", ".join(b.value for b in BackendType)
            raise RuntimeError(
                f"omop_emb.backend={omop_emb.backend!r} is not a supported embedding "
                f"backend. Supported backends: {supported}. FAISS is a query-time cache "
                "accelerator (set omop_emb.faiss_cache_dir over a sqlitevec or pgvector "
                "backend and install the 'embedding-faiss' extra), not a standalone backend."
            ) from None

        def build_backend() -> EmbeddingBackend:
            backend_type = BackendType(omop_emb.backend)
            if backend_type is BackendType.SQLITEVEC:
                from omop_emb.backends.sqlitevec import SQLiteVecEmbeddingBackend
                if omop_emb.sqlite_path is None:
                    raise RuntimeError(
                        "omop_emb.sqlite_path is required when backend='sqlitevec'"
                    )
                return SQLiteVecEmbeddingBackend.from_path(omop_emb.sqlite_path)
            if backend_type is BackendType.PGVECTOR:
                from omop_emb.backends.pgvector import PGVectorEmbeddingBackend
                if config.emb_engine is None:
                    raise RuntimeError(
                        "An embedding resource is required when omop_emb.backend='pgvector'. "
                        "Run 'omop-config configure omop_emb' to provision it."
                    )
                return PGVectorEmbeddingBackend(emb_engine=config.emb_engine)
            raise RuntimeError(
                f"Embedding backend {omop_emb.backend!r} is not supported. "
                "Supported values: sqlitevec, pgvector."
            )

        client_factory: Callable[[str], EmbeddingClient] | None = None
        if omop_emb.api_base and omop_emb.api_key:
            api_base = omop_emb.api_base
            api_key = omop_emb.api_key
            provider_type = omop_emb.provider_type

            def build_client(model_name: str) -> EmbeddingClient:
                return EmbeddingClient(
                    model=model_name,
                    api_base=api_base,
                    api_key=api_key,
                    provider_type=ProviderType(provider_type),
                )

            client_factory = build_client

        adapters.omop_emb = OmopEmbAdapter(
            backend_factory=build_backend,
            backend_type=omop_emb.backend,
            default_model_name=omop_emb.embedding_model,
            client_factory=client_factory,
            cdm_engine=cdm_engine,
            faiss_cache_dir=omop_emb.faiss_cache_dir,
        )

        if client_factory is not None and adapters.omop_graph is not None:
            try:
                model_name = adapters.omop_emb.resolve_model_name()
                adapters.omop_graph.set_embedding_client(
                    adapters.omop_emb.get_client_for_model(model_name),
                    model_name=model_name,
                )
            except Exception as exc:
                _logger.warning(
                    "Embedding client could not be wired into OmopGraphAdapter; "
                    "the embedding tier of concept_ground will be inactive. Reason: %s",
                    exc,
                )

    llm_config = config.llm
    if llm_config.enabled:
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
            kwargs["max_retries"] = 0
            kwargs["timeout"] = 30.0
            return OpenAI(**kwargs)

        adapters.llm = LLMAdapter(
            provider=llm_config.provider,
            default_model_name=llm_config.default_model_name,
            client_factory=build_llm_client,
        )

    return adapters


def build_services(config: AppConfig, adapters: Adapters) -> Services:
    services = Services()
    assisted_classifier = None
    if adapters.llm is not None and config.source_planning.llm_assisted_enabled:
        assisted_classifier = AssistedColumnRoleClassifier(adapters.llm)
    services.source_planning = SourcePlanningService(assisted_classifier=assisted_classifier)
    if adapters.omop_graph is not None:
        services.graph = GraphService(adapters.omop_graph)
        services.grounding = ConceptGroundingService(
            services.graph,
            min_fulltext_overlap=config.grounding.min_fulltext_overlap,
        )
    if adapters.cdm is not None:
        services.vocab = VocabService(adapters.cdm)
        services.mapping = MappingService(
            services.vocab,
            graph_service=services.graph,
            emb_adapter=adapters.omop_emb,
            grounding_service=services.grounding,
        )
    if adapters.llm is not None:
        services.text = TextService(adapters.llm)
        services.domain = DomainService(adapters.llm)
    return services


def build_application(config: AppConfig) -> GroundworkersApp:
    adapters = build_adapters(config)
    return GroundworkersApp(
        config=config,
        adapters=adapters,
        services=build_services(config, adapters),
    )
