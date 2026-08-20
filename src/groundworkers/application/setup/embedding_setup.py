from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from oa_configurator import (
    ResolvedModel,
    Resolver,
    safe_endpoint,  # type: ignore[import-untyped]
)
from omop_emb import EmbeddingBackend, MetricType
from omop_llm import (
    ModelBackend,
    build_model_backend_from_resolved,
    canonical_model_name,
)

from groundworkers.application.setup.databases import classify_connection_error
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    DiagnosticSeverity,
    EmbeddingConfiguration,
    EmbeddingStoreSnapshot,
    EmbeddingStoreState,
    ModelDiagnostic,
    ModelReconciliation,
    ProviderCapabilities,
    ProviderSnapshot,
    RegisteredEmbeddingModel,
)
from groundworkers.base.results import enum_value, required_enum_value
from groundworkers.config import GroundworkersConfig

ModelBackendFactory = Callable[[ResolvedModel], ModelBackend]
ModelInventoryDiscoverer = Callable[[ResolvedModel], Sequence[str]]


def initialize_embedding_store(
    snapshot: ConfigurationSnapshot,
    *,
    initializer: Callable[[object], EmbeddingBackend] | None = None,
) -> EmbeddingStoreSnapshot:
    """Run the reviewed store-initialization operation.

    This is the only setup service that is allowed to construct the writable
    omop-emb backend. Coverage, model checks, and registry inspection use the
    read-only omop-emb inspection API instead.
    """

    configuration = load_embedding_configuration(snapshot)
    if configuration is None or snapshot.stack is None:
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNCONFIGURED,
            backend=None,
            reachable=False,
        )
    try:
        groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
        if groundworkers.vector_store_name is None:
            raise ValueError("An embedding vector store is not configured.")
        resolved = Resolver(snapshot.stack).resolve_vector_store(
            groundworkers.vector_store_name
        )
        if initializer is None:
            from omop_emb.backends import initialize_resolved_vector_store

            initializer = initialize_resolved_vector_store
        backend = initializer(resolved)
        try:
            records = backend.get_registered_models()
            models = tuple(
                RegisteredEmbeddingModel(
                    model_name=record.model_name,
                    provider=required_enum_value(record.provider_type),
                    dimensions=int(record.dimensions),
                    metric=(
                        enum_value(record.metric_type)
                        if record.metric_type is not None
                        else None
                    ),
                    index_type=required_enum_value(record.index_type),
                    has_embeddings=backend.has_any_embeddings(
                        model_name=record.model_name,
                        metric_type=record.metric_type or MetricType.COSINE,
                        _model_record=record,
                    ),
                )
                for record in records
            )
        finally:
            engine = getattr(backend, "emb_engine", None)
            if engine is not None:
                engine.dispose()
    except Exception as exc:
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNREACHABLE,
            backend=configuration.backend,
            reachable=False,
            failure=classify_connection_error(exc),
        )
    return EmbeddingStoreSnapshot(
        state=(
            EmbeddingStoreState.POPULATED
            if any(model.has_embeddings for model in models)
            else EmbeddingStoreState.EMPTY
        ),
        backend=configuration.backend,
        reachable=True,
        models=models,
    )


def load_embedding_configuration(
    snapshot: ConfigurationSnapshot,
) -> EmbeddingConfiguration | None:
    if snapshot.stack is None:
        return None
    stack = snapshot.stack
    try:
        groundworkers = GroundworkersConfig.validate_candidate(stack)
        if (
            groundworkers.embedding_model_name is None
            or groundworkers.vector_store_name is None
        ):
            return None
        resolver = Resolver(stack)
        model = resolver.resolve_model(groundworkers.embedding_model_name)
        vector_store = resolver.resolve_vector_store(
            groundworkers.vector_store_name
        )
    except (KeyError, TypeError, ValueError):
        return None

    vector_store_config = stack.vector_stores[groundworkers.vector_store_name]
    database_config = stack.databases[vector_store_config.database]
    connection_name = database_config.connection
    connection_config = stack.connections[connection_name]
    database_path = (
        connection_config.database_name
        if connection_config.dialect.startswith("sqlite")
        else None
    )
    config_base = snapshot.path.parent if snapshot.path is not None else None
    database_path_exists = (
        _path_exists(database_path, base=config_base)
        if database_path is not None
        else None
    )
    faiss_cache_dir_exists = (
        _path_exists(vector_store.faiss_cache_dir, base=config_base)
        if vector_store.faiss_cache_dir is not None
        else None
    )
    return EmbeddingConfiguration(
        backend=vector_store.backend_type,
        vector_store_name=vector_store.name,
        database_name=vector_store.database.name,
        connection_name=vector_store.database.connection.name,
        database_safe_url=vector_store.database.connection.safe_url,
        provider_name=model.provider.name,
        provider_kind=model.provider.provider,
        model_entry_name=model.name,
        model_name=model.model,
        embeddings_supported=model.embeddings,
        api_base=(safe_endpoint(model.provider.base_url) if model.provider.base_url else None),
        database_path=database_path,
        database_path_exists=database_path_exists,
        faiss_cache_dir=vector_store.faiss_cache_dir,
        faiss_cache_dir_exists=faiss_cache_dir_exists,
    )


def probe_embedding_store(
    backend_factory: Callable[[], EmbeddingBackend] | None,
    *,
    backend_type: str | None,
) -> EmbeddingStoreSnapshot:
    """Prove store reachability independently from population state."""

    if backend_factory is None:
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNCONFIGURED,
            backend=backend_type,
            reachable=False,
        )
    try:
        backend = backend_factory()
        records = backend.get_registered_models()
        models = []
        for record in records:
            metric = record.metric_type or MetricType.COSINE
            has_embeddings = backend.has_any_embeddings(
                model_name=record.model_name,
                metric_type=metric,
                _model_record=record,
            )
            models.append(
                RegisteredEmbeddingModel(
                    model_name=record.model_name,
                    provider=required_enum_value(record.provider_type),
                    dimensions=int(record.dimensions),
                    metric=(
                        enum_value(record.metric_type)
                        if record.metric_type is not None
                        else None
                    ),
                    index_type=required_enum_value(record.index_type),
                    has_embeddings=has_embeddings,
                    concept_count=0 if not has_embeddings else None,
                )
            )
    except Exception as exc:
        # Broad except: converted to a safe setup state.
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNREACHABLE,
            backend=backend_type,
            reachable=False,
            failure=classify_connection_error(exc),
        )
    return EmbeddingStoreSnapshot(
        state=(
            EmbeddingStoreState.POPULATED
            if any(model.has_embeddings for model in models)
            else EmbeddingStoreState.EMPTY
        ),
        backend=backend_type or enum_value(backend.backend_type),
        reachable=True,
        models=tuple(models),
    )


def probe_provider(
    resolved_model: ResolvedModel,
    *,
    backend_factory: ModelBackendFactory = build_model_backend_from_resolved,
    inventory_discoverer: ModelInventoryDiscoverer | None = None,
) -> ProviderSnapshot:
    """Probe one resolved model through omop-llm's provider-neutral backend."""

    inventory: tuple[str, ...] | None = None
    if inventory_discoverer is not None:
        try:
            inventory = tuple(
                canonical_model_name(resolved_model.provider.provider, model_name)
                for model_name in inventory_discoverer(resolved_model)
            )
        except Exception:
            # Broad except: encode is the decisive probe.
            inventory = None

    try:
        backend = backend_factory(resolved_model)
    except Exception as exc:
        # Broad except: converted to a safe setup state.
        return ProviderSnapshot(
            provider_name=resolved_model.provider.name,
            provider_kind=resolved_model.provider.provider,
            model_entry_name=resolved_model.name,
            api_base=(
                safe_endpoint(resolved_model.provider.base_url)
                if resolved_model.provider.base_url
                else None
            ),
            # Canonical, as every other exit from this function is: the name is
            # compared against the embedding store's registry, which holds
            # canonical names, so an unreachable provider must not report a
            # differently-spelled model from a reachable one.
            configured_model=_canonical_or_raw(resolved_model),
            capabilities=ProviderCapabilities(
                list_models=inventory_discoverer is not None,
                encode_probe=resolved_model.embeddings,
                reported_dimensions=resolved_model.embedding_dim is not None,
            ),
            reachable=False,
            encoding_succeeded=False,
            inventory=inventory,
            failure=classify_connection_error(exc),
        )

    capabilities = ProviderCapabilities(
        list_models=inventory_discoverer is not None,
        encode_probe=backend.capabilities.embeddings,
        reported_dimensions=resolved_model.embedding_dim is not None,
    )
    if not backend.is_available():
        return ProviderSnapshot(
            provider_name=resolved_model.provider.name,
            provider_kind=backend.provider,
            model_entry_name=resolved_model.name,
            api_base=(
                safe_endpoint(resolved_model.provider.base_url)
                if resolved_model.provider.base_url
                else None
            ),
            configured_model=backend.model,
            capabilities=capabilities,
            reachable=False,
            encoding_succeeded=False,
            inventory=inventory,
            failure=classify_connection_error(ConnectionError("provider unavailable")),
        )

    if not backend.capabilities.embeddings:
        return ProviderSnapshot(
            provider_name=resolved_model.provider.name,
            provider_kind=backend.provider,
            model_entry_name=resolved_model.name,
            api_base=(
                safe_endpoint(resolved_model.provider.base_url)
                if resolved_model.provider.base_url
                else None
            ),
            configured_model=backend.model,
            capabilities=capabilities,
            reachable=True,
            encoding_succeeded=False,
            inventory=inventory,
        )

    try:
        dimensions = backend.dimensions()
    except Exception as exc:
        # Broad except: converted to a safe setup state.
        return ProviderSnapshot(
            provider_name=resolved_model.provider.name,
            provider_kind=backend.provider,
            model_entry_name=resolved_model.name,
            api_base=(
                safe_endpoint(resolved_model.provider.base_url)
                if resolved_model.provider.base_url
                else None
            ),
            configured_model=backend.model,
            capabilities=capabilities,
            reachable=True,
            encoding_succeeded=False,
            inventory=inventory,
            failure=classify_connection_error(exc),
        )
    return ProviderSnapshot(
        provider_name=resolved_model.provider.name,
        provider_kind=backend.provider,
        model_entry_name=resolved_model.name,
        api_base=(
            safe_endpoint(resolved_model.provider.base_url)
            if resolved_model.provider.base_url
            else None
        ),
        configured_model=backend.model,
        capabilities=capabilities,
        reachable=True,
        encoding_succeeded=True,
        dimensions=dimensions,
        inventory=inventory,
    )


def _canonical_or_raw(resolved_model: ResolvedModel) -> str:
    """The model's canonical name, or its configured spelling if unrecognised."""

    try:
        return canonical_model_name(
            resolved_model.provider.provider, resolved_model.model
        )
    except Exception:
        # Broad except: an unregistered provider key is a configuration problem
        # reported elsewhere, not a reason to lose the snapshot.
        return resolved_model.model


def reconcile_models(
    *,
    configured_model: str | None,
    registered_models: Sequence[RegisteredEmbeddingModel],
    provider: ProviderSnapshot | None,
    store: EmbeddingStoreSnapshot | None = None,
) -> ModelReconciliation:
    """Judge the configured model against the store that must hold its vectors
    and the provider that must produce them.

    *store* is optional only for callers that already know the registry was
    read successfully. Without it an unreachable store is indistinguishable from
    an empty one, and the empty reading is the reassuring one.
    """
    diagnostics: list[ModelDiagnostic] = []
    registered = tuple(registered_models)
    selected = next(
        (item for item in registered if item.model_name == configured_model), None
    )

    if store is not None and not store.reachable:
        diagnostics.append(
            _diagnostic(
                "store_unreachable",
                DiagnosticSeverity.ERROR,
                "The embedding store could not be reached, so what it holds is unknown."
                + (f" {store.failure.detail}" if store.failure is not None else ""),
            )
        )

    if configured_model is None and len(registered) > 1:
        diagnostics.append(
            _diagnostic(
                "multiple_models_no_default",
                DiagnosticSeverity.ERROR,
                "Multiple models are registered but no effective default is configured.",
            )
        )
    elif configured_model is not None and selected is None:
        diagnostics.append(
            _diagnostic(
                "configured_model_unregistered",
                DiagnosticSeverity.INFO,
                "The configured model is ready to be registered by the population plan.",
            )
        )

    for model in registered:
        if configured_model is not None and model.model_name != configured_model:
            diagnostics.append(
                _diagnostic(
                    "registered_model_not_selected",
                    DiagnosticSeverity.WARNING,
                    f"Registered model {model.model_name!r} is not selected by configuration.",
                )
            )

    if configured_model is not None and provider is None:
        diagnostics.append(
            _diagnostic(
                "provider_unconfigured",
                DiagnosticSeverity.ERROR,
                "An encoding provider is required to populate the configured model.",
            )
        )
    elif provider is not None:
        if not provider.reachable:
            # Checked before the capability and encode branches below, which both
            # read as verdicts about the model. An endpoint that never answered
            # has told us nothing about the model, and sends the operator at the
            # wrong fix.
            diagnostics.append(
                _diagnostic(
                    "provider_unreachable",
                    DiagnosticSeverity.ERROR,
                    "The provider endpoint could not be reached."
                    + (
                        f" {provider.failure.detail}"
                        if provider.failure is not None
                        else ""
                    ),
                )
            )
        elif not provider.capabilities.encode_probe:
            diagnostics.append(
                _diagnostic(
                    "provider_embeddings_unsupported",
                    DiagnosticSeverity.ERROR,
                    "The selected model is not configured for embedding operations.",
                )
            )
        elif not provider.encoding_succeeded:
            diagnostics.append(
                _diagnostic(
                    "provider_encode_failed",
                    DiagnosticSeverity.ERROR,
                    "The provider could not encode with the configured model.",
                )
            )
        elif provider.inventory is None:
            diagnostics.append(
                _diagnostic(
                    "provider_inventory_unavailable",
                    DiagnosticSeverity.INFO,
                    "The provider does not expose model inventory; encoding succeeded.",
                )
            )
        elif provider.model_available is False:
            diagnostics.append(
                _diagnostic(
                    "configured_model_absent",
                    DiagnosticSeverity.ERROR,
                    "The configured model is absent from provider inventory.",
                )
            )

        if (
            selected is not None
            and provider.dimensions is not None
            and selected.dimensions != provider.dimensions
        ):
            diagnostics.append(
                _diagnostic(
                    "dimension_mismatch",
                    DiagnosticSeverity.ERROR,
                    "The provider vector dimensions do not match the registered model.",
                )
            )

    return ModelReconciliation(
        configured_model=configured_model,
        registered_models=registered,
        provider=provider,
        diagnostics=tuple(diagnostics),
        store=store,
    )


def _diagnostic(
    code: str, severity: DiagnosticSeverity, message: str
) -> ModelDiagnostic:
    return ModelDiagnostic(code=code, severity=severity, message=message)


def _path_exists(value: str, *, base: Path | None) -> bool:
    if value == ":memory:":
        return True
    path = Path(value).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.exists()
