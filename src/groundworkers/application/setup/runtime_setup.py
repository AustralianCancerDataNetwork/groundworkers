from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from oa_configurator import StackConfig  # type: ignore[import-untyped]

from groundworkers.application.setup.databases import classify_connection_error
from groundworkers.application.setup.models import (
    ChatConfiguration,
    ConfigurationSnapshot,
    DiagnosticSeverity,
    GraphConfiguration,
    LlmModelMetadata,
    LlmProviderCheckResult,
    LlmProviderConfiguration,
    ResourceDiagnostic,
)
from groundworkers.config import GroundworkersConfig, LLMConfig

_LLM_STATUS_TIMEOUT_SECONDS = 2.0


def load_graph_configuration(
    snapshot: ConfigurationSnapshot,
) -> GraphConfiguration | None:
    """Resolve Groundworkers' graph and grounding policy without building an adapter.

    Reads only Groundworkers-owned configuration. It previously validated
    omop-graph's own package config to display that package's traversal limits;
    those limits became per-call arguments in omop-graph 2.x, so the values were
    inert and reading another package's internal config class is not permitted.
    """

    if not snapshot.usable or snapshot.stack is None:
        return None
    # An absent package section means Groundworkers is not configured for this stack;
    # its schema defaults are not an operator's configuration.
    if GroundworkersConfig.tool_name not in snapshot.stack.tools:
        return None
    try:
        groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
        database = snapshot.stack.databases.get(groundworkers.cdm_db)
    except (KeyError, TypeError, ValueError):
        return None
    return GraphConfiguration(
        cdm_database_name=groundworkers.cdm_db,
        vocabulary_schema=getattr(database, "vocab_schema", None),
        grounding_max_depth=groundworkers.grounding.max_depth,
        min_fulltext_overlap=groundworkers.grounding.min_fulltext_overlap,
    )


def load_llm_provider_configuration(
    snapshot: ConfigurationSnapshot,
) -> LlmProviderConfiguration | None:
    """Resolve the provider endpoint and defaults without exposing credentials."""

    if not snapshot.usable or snapshot.stack is None:
        return None
    if GroundworkersConfig.tool_name not in snapshot.stack.tools:
        return None
    try:
        tool = _effective_tool(snapshot.stack, GroundworkersConfig.tool_name)
        config = GroundworkersConfig.model_validate(tool or {})
        llm = config.llm
    except (KeyError, TypeError, ValueError):
        return None
    return LlmProviderConfiguration(
        enabled=llm.enabled,
        provider=llm.provider,
        api_base=_safe_api_base(llm.api_base) if llm.api_base else None,
        credentials_configured=bool(llm.api_key),
        default_model_name=llm.default_model_name,
    )


def verify_llm_provider(
    snapshot: ConfigurationSnapshot,
    *,
    client_factory: Callable[[LLMConfig], Any] | None = None,
    metadata_factory: Callable[[LLMConfig], tuple[LlmModelMetadata, ...]] | None = None,
) -> LlmProviderCheckResult | None:
    """Check the configured LLM endpoint and selected model without completing text."""

    loaded = _load_llm_config(snapshot)
    if loaded is None:
        return None
    configuration, llm = loaded
    return verify_llm_config(
        llm,
        configuration=configuration,
        client_factory=client_factory,
        metadata_factory=metadata_factory,
    )


def verify_llm_config(
    llm: LLMConfig,
    *,
    configuration: LlmProviderConfiguration | None = None,
    client_factory: Callable[[LLMConfig], Any] | None = None,
    metadata_factory: Callable[[LLMConfig], tuple[LlmModelMetadata, ...]] | None = None,
    require_default_model: bool = True,
) -> LlmProviderCheckResult:
    """Check a concrete LLM config, including unsaved setup wizard drafts."""

    configuration = configuration or LlmProviderConfiguration(
        enabled=llm.enabled,
        provider=llm.provider,
        api_base=_safe_api_base(llm.api_base) if llm.api_base else None,
        credentials_configured=bool(llm.api_key),
        default_model_name=llm.default_model_name,
    )
    if not configuration.enabled:
        return LlmProviderCheckResult(
            provider=configuration.provider,
            api_base=configuration.api_base,
            default_model_name=configuration.default_model_name,
            reachable=False,
            diagnostics=(
                ResourceDiagnostic(
                    "llm_disabled",
                    "The LLM provider is disabled.",
                    DiagnosticSeverity.WARNING,
                ),
            ),
        )

    diagnostics: list[ResourceDiagnostic] = []
    if require_default_model and configuration.default_model_name is None:
        diagnostics.append(
            ResourceDiagnostic(
                "llm_model_missing",
                "No default LLM model is configured.",
                DiagnosticSeverity.ERROR,
            )
        )

    try:
        client = (client_factory or _openai_client)(llm)
        response = client.models.list(timeout=_LLM_STATUS_TIMEOUT_SECONDS)
        inventory = tuple(str(item.id) for item in response.data)
    except Exception as exc:  # noqa: BLE001 - converted to redacted setup state
        failure = classify_connection_error(exc)
        return LlmProviderCheckResult(
            provider=configuration.provider,
            api_base=configuration.api_base,
            default_model_name=configuration.default_model_name,
            reachable=False,
            failure=failure,
            diagnostics=(
                *diagnostics,
                ResourceDiagnostic(
                    "llm_endpoint_unreachable",
                    failure.detail,
                    DiagnosticSeverity.ERROR,
                ),
            ),
        )

    diagnostics.append(
        ResourceDiagnostic(
            "llm_endpoint_reachable",
            "The LLM provider endpoint responded to model inventory.",
        )
    )
    model_metadata: tuple[LlmModelMetadata, ...] = ()
    if _uses_ollama_inventory(llm):
        try:
            model_metadata = (
                metadata_factory(llm)
                if metadata_factory is not None
                else _ollama_model_metadata(llm)
            )
            diagnostics.append(
                ResourceDiagnostic(
                    "llm_ollama_metadata_available",
                    "Ollama model metadata is available.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - enrichment must not block readiness
            diagnostics.append(
                ResourceDiagnostic(
                    "llm_ollama_metadata_unavailable",
                    f"Ollama model metadata could not be read: {exc}",
                    DiagnosticSeverity.WARNING,
                )
            )
    inventory = _merge_model_inventory(inventory, model_metadata)
    model_available: bool | None = None
    if configuration.default_model_name is not None:
        model_available = configuration.default_model_name in inventory
        if model_available:
            diagnostics.append(
                ResourceDiagnostic(
                    "llm_model_available",
                    f"Configured model {configuration.default_model_name!r} is available.",
                )
            )
        else:
            diagnostics.append(
                ResourceDiagnostic(
                    "llm_model_unavailable",
                    f"Configured model {configuration.default_model_name!r} was not returned by provider inventory.",
                    DiagnosticSeverity.ERROR,
                )
            )
    return LlmProviderCheckResult(
        provider=configuration.provider,
        api_base=configuration.api_base,
        default_model_name=configuration.default_model_name,
        reachable=True,
        model_available=model_available,
        inventory=inventory,
        model_metadata=model_metadata,
        diagnostics=tuple(diagnostics),
    )


def load_chat_configuration(
    provider: LlmProviderConfiguration | None,
) -> ChatConfiguration | None:
    """Return the selected chat model once provider configuration is usable."""

    if provider is None or not provider.ready_for_chat:
        return None
    assert provider.default_model_name is not None
    return ChatConfiguration(
        provider=provider.provider,
        model_name=provider.default_model_name,
    )


def _load_llm_config(
    snapshot: ConfigurationSnapshot,
) -> tuple[LlmProviderConfiguration, LLMConfig] | None:
    if not snapshot.usable or snapshot.stack is None:
        return None
    if GroundworkersConfig.tool_name not in snapshot.stack.tools:
        return None
    try:
        tool = _effective_tool(snapshot.stack, GroundworkersConfig.tool_name)
        config = GroundworkersConfig.model_validate(tool or {})
        llm = config.llm
    except (KeyError, TypeError, ValueError):
        return None
    return (
        LlmProviderConfiguration(
            enabled=llm.enabled,
            provider=llm.provider,
            api_base=_safe_api_base(llm.api_base) if llm.api_base else None,
            credentials_configured=bool(llm.api_key),
            default_model_name=llm.default_model_name,
        ),
        llm,
    )


def _openai_client(llm: LLMConfig) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The 'openai' package is required for LLM provider checks. "
            "Install it with: pip install openai"
        ) from exc
    kwargs: dict[str, Any] = {"max_retries": 0}
    if llm.api_key is not None:
        kwargs["api_key"] = llm.api_key
    if llm.api_base is not None:
        kwargs["base_url"] = llm.api_base
    return OpenAI(**kwargs)


def _uses_ollama_inventory(llm: LLMConfig) -> bool:
    provider = llm.provider.lower()
    if provider == "ollama":
        return True
    api_base = llm.api_base or ""
    return "localhost:11434" in api_base or "127.0.0.1:11434" in api_base


def _ollama_model_metadata(llm: LLMConfig) -> tuple[LlmModelMetadata, ...]:
    base_url = _ollama_native_base_url(llm.api_base)
    with urlopen(
        f"{base_url}/api/tags",
        timeout=_LLM_STATUS_TIMEOUT_SECONDS,
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    models = payload.get("models", ())
    return tuple(
        _ollama_metadata_item(item) for item in models if isinstance(item, dict)
    )


def _ollama_native_base_url(api_base: str | None) -> str:
    if not api_base:
        return "http://localhost:11434"
    parts = urlsplit(api_base)
    path = parts.path.rstrip("/")
    path = path.removesuffix("/v1")
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _ollama_metadata_item(item: dict[str, Any]) -> LlmModelMetadata:
    details = item.get("details")
    if not isinstance(details, dict):
        details = {}
    name = str(item.get("model") or item.get("name") or "")
    return LlmModelMetadata(
        name=name,
        size_bytes=_optional_int(item.get("size")),
        modified_at=_optional_str(item.get("modified_at")),
        digest=_optional_str(item.get("digest")),
        parameter_size=_optional_str(details.get("parameter_size")),
        quantization_level=_optional_str(details.get("quantization_level")),
        family=_optional_str(details.get("family")),
        format=_optional_str(details.get("format")),
    )


def _merge_model_inventory(
    inventory: tuple[str, ...],
    model_metadata: tuple[LlmModelMetadata, ...],
) -> tuple[str, ...]:
    merged = list(inventory)
    seen = set(inventory)
    for item in model_metadata:
        if item.name and item.name not in seen:
            merged.append(item.name)
            seen.add(item.name)
    return tuple(merged)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _effective_tool(stack: StackConfig, name: str):
    return stack.tools.get(name)


def _safe_api_base(api_base: str) -> str:
    parts = urlsplit(api_base)
    hostname = parts.hostname or ""
    netloc = hostname if parts.port is None else f"{hostname}:{parts.port}"
    pairs = []
    for item in parts.query.split("&") if parts.query else ():
        key, separator, value = item.partition("=")
        if any(
            token in key.lower() for token in ("key", "token", "secret", "password")
        ):
            value = "%2A%2A%2A"
        pairs.append(f"{key}{separator}{value}")
    return urlunsplit(
        (parts.scheme, netloc, parts.path, "&".join(pairs), parts.fragment)
    )
