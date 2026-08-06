from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from omop_emb import EmbeddingBackend, EmbeddingClient
from omop_emb.config import MetricType, OmopEmbConfig, ProviderType
from omop_emb.embeddings.embedding_client import EmbeddingRole

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


class ProviderProbeAdapter(Protocol):
    provider_kind: str
    api_base: str
    model_name: str
    capabilities: ProviderCapabilities

    def list_models(self) -> Sequence[str]: ...

    def encode_probe(self) -> int: ...


class OpenAICompatibleProviderAdapter:
    """Probe an Ollama, OpenAI, or compatible embeddings endpoint."""

    def __init__(
        self,
        *,
        provider_kind: str,
        api_base: str,
        api_key: str,
        model_name: str,
        supports_model_listing: bool = False,
        pull_model: Callable[[str], None] | None = None,
    ) -> None:
        provider_type = (
            ProviderType.OLLAMA
            if provider_kind == ProviderType.OLLAMA.value
            else ProviderType.OPENAI
        )
        self.provider_kind = provider_kind
        self.api_base = safe_api_base(api_base)
        self.model_name = model_name
        self._client = EmbeddingClient(
            model=model_name,
            api_base=api_base,
            api_key=api_key,
            provider_type=provider_type,
        )
        self._pull_model = pull_model
        self.capabilities = ProviderCapabilities(
            list_models=supports_model_listing,
            pull_model=pull_model is not None,
            reported_dimensions=provider_type is ProviderType.OLLAMA,
        )

    def list_models(self) -> Sequence[str]:
        if not self.capabilities.list_models:
            raise NotImplementedError(
                "This provider does not advertise model inventory."
            )
        response = self._client.base_client.models.list()
        return tuple(str(item.id) for item in response.data)

    def encode_probe(self) -> int:
        vectors = self._client.embeddings(
            "OMOP embedding setup probe",
            embedding_role=EmbeddingRole.QUERY,
            batch_size=1,
        )
        return int(vectors.shape[1])

    def pull_model(self) -> None:
        if self._pull_model is None:
            raise NotImplementedError("This provider does not advertise model pulling.")
        self._pull_model(self.model_name)


def load_embedding_configuration(
    snapshot: ConfigurationSnapshot,
) -> EmbeddingConfiguration | None:
    if snapshot.stack is None:
        return None
    stack = snapshot.stack
    configured = "omop_emb" in stack.tools or bool(
        stack.active_profile
        and stack.active_profile in stack.profiles
        and "omop_emb" in stack.profiles[stack.active_profile].tools
    )
    if not configured:
        return None
    try:
        config = OmopEmbConfig.from_stack(stack)
    except (KeyError, TypeError, ValueError):
        return None
    config_base = snapshot.path.parent if snapshot.path is not None else None
    sqlite_path_exists = (
        _path_exists(config.sqlite_path, base=config_base)
        if config.sqlite_path is not None
        else None
    )
    faiss_cache_dir_exists = (
        _path_exists(config.faiss_cache_dir, base=config_base)
        if config.faiss_cache_dir is not None
        else None
    )
    return EmbeddingConfiguration(
        backend=config.backend,
        provider_kind=_enum_value(config.provider_type),
        model_name=config.embedding_model,
        api_base=safe_api_base(config.api_base),
        sqlite_path=config.sqlite_path,
        sqlite_path_exists=sqlite_path_exists,
        faiss_cache_dir=config.faiss_cache_dir,
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
            )
            models.append(
                RegisteredEmbeddingModel(
                    model_name=record.model_name,
                    provider=_enum_value(record.provider_type),
                    dimensions=int(record.dimensions),
                    metric=(
                        _enum_value(record.metric_type)
                        if record.metric_type is not None
                        else None
                    ),
                    index_type=_enum_value(record.index_type),
                    has_embeddings=has_embeddings,
                    concept_count=0 if not has_embeddings else None,
                )
            )
    except Exception as exc:  # noqa: BLE001 - converted to a safe setup state
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
        backend=backend_type or _enum_value(backend.backend_type),
        reachable=True,
        models=tuple(models),
    )


def probe_provider(adapter: ProviderProbeAdapter) -> ProviderSnapshot:
    """Run optional inventory and a bounded encode probe."""

    inventory: tuple[str, ...] | None = None
    inventory_reachable = False
    if adapter.capabilities.list_models:
        try:
            inventory = tuple(adapter.list_models())
            inventory_reachable = True
        except Exception:  # noqa: BLE001 - encode is the decisive probe
            inventory = None

    try:
        dimensions = adapter.encode_probe()
    except Exception as exc:  # noqa: BLE001 - converted to a safe setup state
        return ProviderSnapshot(
            provider_kind=adapter.provider_kind,
            api_base=adapter.api_base,
            configured_model=adapter.model_name,
            capabilities=adapter.capabilities,
            reachable=inventory_reachable,
            encoding_succeeded=False,
            inventory=inventory,
            failure=classify_connection_error(exc),
        )
    return ProviderSnapshot(
        provider_kind=adapter.provider_kind,
        api_base=adapter.api_base,
        configured_model=adapter.model_name,
        capabilities=adapter.capabilities,
        reachable=True,
        encoding_succeeded=True,
        dimensions=dimensions,
        inventory=inventory,
    )


def reconcile_models(
    *,
    configured_model: str | None,
    registered_models: Sequence[RegisteredEmbeddingModel],
    provider: ProviderSnapshot | None,
) -> ModelReconciliation:
    diagnostics: list[ModelDiagnostic] = []
    registered = tuple(registered_models)
    selected = next(
        (item for item in registered if item.model_name == configured_model), None
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
        if not provider.encoding_succeeded:
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
    )


def _diagnostic(
    code: str, severity: DiagnosticSeverity, message: str
) -> ModelDiagnostic:
    return ModelDiagnostic(code=code, severity=severity, message=message)


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _path_exists(value: str, *, base: Path | None) -> bool:
    if value == ":memory:":
        return True
    path = Path(value).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.exists()


def safe_api_base(api_base: str) -> str:
    parts = urlsplit(api_base)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    safe_query = urlencode(
        tuple(
            (key, "***" if _sensitive_key(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        )
    )
    return urlunsplit((parts.scheme, host, parts.path, safe_query, ""))


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("key", "secret", "password", "token"))
