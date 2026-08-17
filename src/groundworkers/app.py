from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.config import AppConfig
from groundworkers.services import (
    ConceptGroundingService,
    DomainService,
    GraphService,
    MappingService,
    TextService,
    VocabService,
)
from groundworkers.services.source_planning import (
    AssistedColumnRoleClassifier,
    SourcePlanningService,
)

if TYPE_CHECKING:
    from omop_emb import EmbeddingBackend
    from omop_llm import ModelBackend


@dataclass
class Adapters:
    cdm: CDMAdapter | None = None
    omop_graph: OmopGraphAdapter | None = None
    omop_emb: OmopEmbAdapter | None = None
    llm: LLMAdapter | None = None
    embedding_configuration_detail: str | None = None


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
    adapters.cdm = CDMAdapter(config.cdm_engine)

    resolved_store = config.vector_store
    resolved_model = config.embedding_model
    shared_backend: EmbeddingBackend | None = None

    def get_embedding_backend() -> EmbeddingBackend:
        nonlocal shared_backend
        if shared_backend is None:
            if resolved_store is None:
                raise RuntimeError("No vector store is configured for Groundworkers.")
            from omop_emb.backends import (
                resolve_backend_from_resolved_vector_store,
            )

            shared_backend = resolve_backend_from_resolved_vector_store(resolved_store)
        return shared_backend

    shared_model_backend: ModelBackend | None = None

    def get_model_backend() -> ModelBackend:
        nonlocal shared_model_backend
        if shared_model_backend is None:
            if resolved_model is None:
                raise RuntimeError("No embedding model is configured for Groundworkers.")
            from omop_llm import build_model_backend_from_resolved

            shared_model_backend = build_model_backend_from_resolved(resolved_model)
        return shared_model_backend

    if resolved_store is not None:
        configuration_detail = None
        if resolved_model is None:
            configuration_detail = (
                "The vector store is configured without an embedding model. "
                "Stored-vector operations remain available; live query encoding is disabled."
            )
        adapters.omop_emb = OmopEmbAdapter(
            backend_factory=get_embedding_backend,
            backend_type=resolved_store.backend_type,
            default_model_name=(resolved_model.model if resolved_model else None),
            model_backend_factory=(get_model_backend if resolved_model else None),
            cdm_engine=config.cdm_engine,
            faiss_cache_dir=resolved_store.faiss_cache_dir,
            configuration_detail=configuration_detail,
        )
        adapters.embedding_configuration_detail = configuration_detail
    elif resolved_model is not None:
        adapters.embedding_configuration_detail = (
            "The embedding model is configured without a vector store. "
            "Configure groundworkers.vector_store_name to enable embedding operations."
        )

    if "omop_graph" in config.stack.tools:
        complete_embedding = resolved_store is not None and resolved_model is not None
        adapters.omop_graph = OmopGraphAdapter(
            engine=config.cdm_engine,
            vocab_schema=config.cdm_database.vocab_schema,
            embedding_backend_factory=(
                get_embedding_backend if complete_embedding else None
            ),
            resolved_embedding_model=(resolved_model if complete_embedding else None),
            faiss_cache_dir=(
                resolved_store.faiss_cache_dir
                if resolved_store is not None and resolved_model is not None
                else None
            ),
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
        # cdm is optional; only the classified-edge traversals
        # (concept_associations / concept_extended_inheritance) require it.
        services.graph = GraphService(adapters.omop_graph, adapters.cdm)
        services.grounding = ConceptGroundingService(
            services.graph,
            min_fulltext_overlap=config.grounding.min_fulltext_overlap,
            max_depth=5,
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
