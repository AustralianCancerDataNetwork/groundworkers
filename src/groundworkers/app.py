from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from oa_configurator import ConfigurationError, Resolver

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.adapters.llm import LLMAdapter
from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.config import AppConfig
from groundworkers.plugins import (
    GroundworkersPlugin,
    GroundworkersPluginReadiness,
    PluginConfigResolver,
    PluginContext,
    PluginReadinessResult,
    PluginReadinessState,
    discover_plugins,
)
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

logger = logging.getLogger(__name__)


@dataclass
class Adapters:
    cdm: CDMAdapter | None = None
    omop_graph: OmopGraphAdapter | None = None
    omop_emb: OmopEmbAdapter | None = None
    llm: LLMAdapter | None = None
    embedding_configuration_detail: str | None = None
    # Shared, lazily-built closures also exposed via PluginContext (see
    # _build_plugin_context) so a plugin reuses the one backend connection
    # instead of opening a second one. None when the corresponding backend
    # is unconfigured, same gating as the adapters above.
    embedding_backend_factory: Callable[[], Any] | None = field(default=None, repr=False)
    embedding_model_backend_factory: Callable[[], Any] | None = field(default=None, repr=False)
    chat_backend_factory: Callable[[], Any] | None = field(default=None, repr=False)


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
    plugins: dict[str, object] = field(default_factory=dict)
    plugin_definitions: tuple[GroundworkersPlugin, ...] = ()
    plugin_issues: dict[str, str] = field(default_factory=dict)


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
                raise RuntimeError("No vector store is configured.")
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
        adapters.embedding_backend_factory = get_embedding_backend
    elif resolved_model is not None:
        adapters.embedding_configuration_detail = (
            "The embedding model is configured without a vector store. "
            "Configure groundworkers.vector_store_name to enable embedding operations."
        )

    if resolved_model is not None:
        adapters.embedding_model_backend_factory = get_model_backend

    # Graph and lexical services use the resolved CDM database directly. A CDM
    # configuration is therefore sufficient; no separate graph section is needed.
    complete_embedding = resolved_store is not None and resolved_model is not None
    adapters.omop_graph = OmopGraphAdapter(
        engine=config.cdm_engine,
        embedding_backend_factory=(
            get_embedding_backend if complete_embedding else None
        ),
        resolved_embedding_model=(resolved_model if complete_embedding else None),
        # Supplies the read-only query encoder the write=False graph cannot build
        # for itself. Shares the one ModelBackend with the embedding adapter.
        model_backend_factory=(get_model_backend if complete_embedding else None),
        faiss_cache_dir=(
            resolved_store.faiss_cache_dir
            if resolved_store is not None and resolved_model is not None
            else None
        ),
    )

    resolved_llm_model = config.llm_model
    if resolved_llm_model is not None:
        chat_backend: ModelBackend | None = None

        def get_chat_backend() -> ModelBackend:
            # Built lazily and separately from the embedding backend above: chat
            # and embeddings are distinct [models.*] entries and may resolve to
            # different providers entirely.
            nonlocal chat_backend
            if chat_backend is None:
                from omop_llm import build_model_backend_from_resolved

                chat_backend = build_model_backend_from_resolved(resolved_llm_model)
            return chat_backend

        adapters.llm = LLMAdapter(backend_factory=get_chat_backend)
        adapters.chat_backend_factory = get_chat_backend

    return adapters


def build_services(config: AppConfig, adapters: Adapters) -> Services:
    services = Services()
    assisted_classifier = None
    if adapters.llm is not None and config.groundworkers.source_planning_llm_assisted_enabled:
        assisted_classifier = AssistedColumnRoleClassifier(adapters.llm)
    services.source_planning = SourcePlanningService(assisted_classifier=assisted_classifier)
    if adapters.omop_graph is not None:
        # cdm is optional; only the classified-edge traversals
        # (concept_associations / concept_extended_inheritance) require it.
        services.graph = GraphService(adapters.omop_graph, adapters.cdm)
        services.grounding = ConceptGroundingService(
            services.graph,
            min_fulltext_overlap=config.groundworkers.grounding_min_fulltext_overlap,
            max_depth=config.groundworkers.grounding_max_depth,
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


def _build_plugin_context(config: AppConfig, adapters: Adapters) -> PluginContext:
    """Assemble the resolved handles every plugin is given.

    `cdm_database`/`vector_store` come straight off `AppConfig`, already
    resolved; the three backend factories are the same lazily-built closures
    `build_adapters` shares between `OmopEmbAdapter`/`OmopGraphAdapter`/
    `LLMAdapter` above, now also exposed on `Adapters` for this purpose.
    """

    return PluginContext(
        resolver=PluginConfigResolver(Resolver(config.stack)),
        cdm_database=config.cdm_database,
        cdm_engine=config.cdm_engine,
        vector_store=config.vector_store,
        embedding_backend_factory=adapters.embedding_backend_factory,
        embedding_model_backend_factory=adapters.embedding_model_backend_factory,
        chat_backend_factory=adapters.chat_backend_factory,
    )


def _build_plugins(
    config: AppConfig,
    context: PluginContext,
    plugin_definitions: tuple[GroundworkersPlugin, ...],
) -> tuple[dict[str, object], dict[str, str]]:
    """Resolve each installed plugin's own config and build its state.

    A plugin with no `config_cls` gets `config=None`. A plugin whose config
    section is absent or fails validation is treated the same as a missing
    optional backend elsewhere in this module: skipped, not fatal to startup.
    """

    plugins: dict[str, object] = {}
    issues: dict[str, str] = {}
    for plugin in plugin_definitions:
        state, issue = _build_plugin(config, context, plugin)
        if state is not None:
            plugins[plugin.name] = state
        else:
            issues[plugin.name] = issue or "plugin prerequisites are unavailable"
    return plugins, issues


def _build_plugin(
    config: AppConfig,
    context: PluginContext,
    plugin: GroundworkersPlugin,
) -> tuple[object | None, str | None]:
    plugin_config = None
    if plugin.config_cls is not None:
        try:
            plugin_config = plugin.config_cls.validate_candidate(config.stack)
        except ConfigurationError as exc:
            logger.info(
                "Plugin %s config not present or invalid, skipping: %s",
                plugin.name,
                exc,
            )
            return None, f"invalid configuration ({type(exc).__name__})"
    try:
        state = plugin.build(context, plugin_config)
    except Exception:
        logger.exception("Groundworkers plugin %s failed to build.", plugin.name)
        return None, "plugin build failed"
    if state is None:
        return None, "plugin prerequisites are unavailable"
    return state, None


def verify_plugin_readiness(
    config: AppConfig,
    plugin: GroundworkersPlugin,
) -> PluginReadinessResult:
    """Build and run one plugin's optional read-only readiness check.

    This is deliberately a headless host operation. TUI code may render its
    result, while MCP tools can return the same contract directly.
    """

    if not isinstance(plugin, GroundworkersPluginReadiness):
        return PluginReadinessResult(
            state=PluginReadinessState.UNCONFIGURED,
            configured=False,
            summary="This plugin does not expose readiness verification.",
        )
    adapters = build_adapters(config)
    context = _build_plugin_context(config, adapters)
    state, issue = _build_plugin(config, context, plugin)
    if state is None:
        return PluginReadinessResult(
            state=PluginReadinessState.UNCONFIGURED,
            configured=False,
            summary=(
                "Plugin configuration or prerequisites are unavailable. "
                f"Host detail: {issue or 'unknown issue'}."
            ),
        )
    try:
        return plugin.verify_readiness(state)
    except Exception:
        logger.exception("Groundworkers plugin %s readiness check failed.", plugin.name)
        return PluginReadinessResult(
            state=PluginReadinessState.ERROR,
            summary="Plugin verification failed unexpectedly; review the host logs.",
        )


def load_plugin_readiness(
    config_path: str,
    plugin: GroundworkersPlugin,
) -> PluginReadinessResult:
    """Load current configuration and verify one plugin without TUI coupling."""

    from groundworkers.bootstrap import build_app_config

    try:
        config = build_app_config(config_path=config_path)
    except Exception:
        logger.info(
            "Could not load configuration while verifying plugin %s.",
            plugin.name,
            exc_info=True,
        )
        return PluginReadinessResult(
            state=PluginReadinessState.UNCONFIGURED,
            configured=False,
            summary=(
                "Create or repair the base Groundworkers configuration, then "
                "configure this plugin."
            ),
        )
    return verify_plugin_readiness(config, plugin)


def build_application(config: AppConfig) -> GroundworkersApp:
    adapters = build_adapters(config)
    context = _build_plugin_context(config, adapters)
    plugin_definitions = tuple(discover_plugins())
    plugins, plugin_issues = _build_plugins(config, context, plugin_definitions)
    return GroundworkersApp(
        config=config,
        adapters=adapters,
        services=build_services(config, adapters),
        plugins=plugins,
        plugin_definitions=plugin_definitions,
        plugin_issues=plugin_issues,
    )
