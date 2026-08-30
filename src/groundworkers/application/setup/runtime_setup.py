from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from oa_configurator import (
    StackConfig,  # type: ignore[import-untyped]
    safe_endpoint,  # type: ignore[import-untyped]
)

from groundworkers.application.setup.databases import classify_connection_error
from groundworkers.application.setup.models import (
    ChatConfiguration,
    ConfigurationSnapshot,
    DiagnosticSeverity,
    GraphConfiguration,
    LlmModelMetadata,
    LlmProviderCheckResult,
    LlmProviderConfiguration,
    LlmProviderDraft,
    ResourceDiagnostic,
)
from groundworkers.config import GroundworkersConfig

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
        grounding_max_depth=groundworkers.grounding_max_depth,
        min_fulltext_overlap=groundworkers.grounding_min_fulltext_overlap,
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
        draft = _draft_from_stack(snapshot.stack, config.llm_model_name)
    except (KeyError, TypeError, ValueError):
        return None
    if draft is None:
        return LlmProviderConfiguration(
            enabled=False,
            provider="",
            api_base=None,
            credentials_configured=False,
            default_model_name=None,
        )
    return _configuration_from_draft(draft, enabled=True)


def verify_llm_provider(
    snapshot: ConfigurationSnapshot,
    *,
    inventory_factory: Callable[[LlmProviderDraft], Sequence[str]] | None = None,
    metadata_factory: Callable[[LlmProviderDraft], tuple[LlmModelMetadata, ...]] | None = None,
) -> LlmProviderCheckResult | None:
    """Check the configured LLM endpoint and selected model without completing text."""

    loaded = _load_llm_config(snapshot)
    if loaded is None:
        return None
    configuration, llm = loaded
    return verify_llm_config(
        llm,
        configuration=configuration,
        inventory_factory=inventory_factory,
        metadata_factory=metadata_factory,
    )


def verify_llm_config(
    llm: LlmProviderDraft,
    *,
    configuration: LlmProviderConfiguration | None = None,
    inventory_factory: Callable[[LlmProviderDraft], Sequence[str]] | None = None,
    metadata_factory: Callable[[LlmProviderDraft], tuple[LlmModelMetadata, ...]] | None = None,
    require_default_model: bool = True,
) -> LlmProviderCheckResult:
    """Check a concrete LLM config, including unsaved setup wizard drafts."""

    # A draft supplied directly is by definition one the operator is configuring
    # right now, so it verifies as enabled.
    configuration = configuration or _configuration_from_draft(llm, enabled=True)
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

    model_metadata: tuple[LlmModelMetadata, ...] = ()
    ollama_metadata_checked = False
    try:
        if inventory_factory is not None:
            inventory = tuple(inventory_factory(llm))
        elif _uses_ollama_inventory(llm):
            # Ollama's model inventory is native /api/tags, not the
            # OpenAI-compatible /models route. Reuse the metadata parser so
            # the setup console accepts the same endpoint shape as the
            # provider backend.
            model_metadata = _ollama_model_metadata(llm)
            ollama_metadata_checked = True
            inventory = tuple(item.name for item in model_metadata)
        else:
            inventory = tuple(_inventory_for_provider(llm))
    except Exception as exc:
        # Broad except: converted to redacted setup state.
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
    if _uses_ollama_inventory(llm):
        if not ollama_metadata_checked:
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
            except Exception as exc:
                # Enrichment must not block readiness when inventory came from
                # an injected discovery service.
                diagnostics.append(
                    ResourceDiagnostic(
                        "llm_ollama_metadata_unavailable",
                        f"Ollama model metadata could not be read: {exc}",
                        DiagnosticSeverity.WARNING,
                    )
                )
        else:
            diagnostics.append(
                ResourceDiagnostic(
                    "llm_ollama_metadata_available",
                    "Ollama model metadata is available.",
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
) -> tuple[LlmProviderConfiguration, LlmProviderDraft] | None:
    if not snapshot.usable or snapshot.stack is None:
        return None
    if GroundworkersConfig.tool_name not in snapshot.stack.tools:
        return None
    try:
        tool = _effective_tool(snapshot.stack, GroundworkersConfig.tool_name)
        config = GroundworkersConfig.model_validate(tool or {})
        draft = _draft_from_stack(snapshot.stack, config.llm_model_name)
    except (KeyError, TypeError, ValueError):
        return None
    if draft is None:
        return None
    return (_configuration_from_draft(draft, enabled=True), draft)


def _draft_from_stack(
    stack: StackConfig,
    model_name: str | None,
) -> LlmProviderDraft | None:
    """Flatten the referenced [models.*] entry and its provider into one draft.

    Returns ``None`` when no chat model is configured, which is what "the LLM is
    off" now means: there is no separate enabled flag.
    """
    if model_name is None:
        return None
    model = stack.models.get(model_name)
    if model is None:
        return None
    provider = stack.providers.get(model.provider)
    if provider is None:
        return None
    return LlmProviderDraft(
        provider=provider.provider,
        api_base=provider.base_url,
        api_key=provider.api_key,
        default_model_name=model.model,
    )


def _configuration_from_draft(
    draft: LlmProviderDraft,
    *,
    enabled: bool,
) -> LlmProviderConfiguration:
    return LlmProviderConfiguration(
        enabled=enabled,
        provider=draft.provider,
        api_base=safe_endpoint(draft.api_base) if draft.api_base else None,
        credentials_configured=bool(draft.api_key),
        default_model_name=draft.default_model_name,
    )


# Base URL a provider serves at when the operator leaves the endpoint blank
# TODO: move these defaults into omop-llm's provider definitions so the setup 
# console can read them from the same source.
_PROVIDER_DEFAULT_BASE_URL: Final = {
    "ollama": "http://localhost:11434",
    "llamacpp": "http://127.0.0.1:8080/v1",
    "vllm": "http://localhost:8000/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

# Providers that speak the OpenAI wire protocol for model listing:
# `GET {base}/models` with `Authorization: Bearer <key>`, against a root
# that ends in `/v1` 
# TODO: this also probably should go to omop-llm
_OPENAI_COMPATIBLE_PROVIDERS: Final = frozenset({"openai", "vllm", "llamacpp"})

_ANTHROPIC_API_VERSION: Final = "2023-06-01"
_GEMINI_DEFAULT_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta"


def _normalized_openai_base(llm: LlmProviderDraft) -> str:
    """Resolve the OpenAI-wire-protocol root for one provider draft.

    Falls back to the per provider documented default when no endpoint given.
    Appends the trailing ``/v1`` segment as required.
    """
    provider = llm.provider.lower()
    base = llm.api_base or _PROVIDER_DEFAULT_BASE_URL.get(provider, "https://api.openai.com/v1")
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _openai_compatible_inventory(llm: LlmProviderDraft) -> tuple[str, ...]:
    """List models from an OpenAI-wire-protocol provider's ``/v1/models`` endpoint.

    Covers ``openai``, ``vllm``, and ``llamacpp``.
      
    A plain HTTP GET rather than the OpenAI SDK, matching
    :func:`_ollama_model_metadata` below. 
    """
    base = _normalized_openai_base(llm)
    request = Request(f"{base}/models", method="GET")
    if llm.api_key:
        request.add_header("Authorization", f"Bearer {llm.api_key}")
    with urlopen(request, timeout=_LLM_STATUS_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("Provider did not return a model list.")
    return tuple(
        str(item["id"]) for item in data if isinstance(item, dict) and "id" in item
    )


def _anthropic_inventory(llm: LlmProviderDraft) -> tuple[str, ...]:
    """List models from Anthropic's native ``/v1/models`` endpoint.
    """
    base = (llm.api_base or _PROVIDER_DEFAULT_BASE_URL["anthropic"]).rstrip("/")
    request = Request(f"{base}/v1/models", method="GET")
    request.add_header("anthropic-version", _ANTHROPIC_API_VERSION)
    if llm.api_key:
        request.add_header("x-api-key", llm.api_key)
    with urlopen(request, timeout=_LLM_STATUS_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("Provider did not return a model list.")
    return tuple(
        str(item["id"]) for item in data if isinstance(item, dict) and "id" in item
    )


def _gemini_inventory(llm: LlmProviderDraft) -> tuple[str, ...]:
    """List models from Gemini's native ``ListModels`` endpoint.
    """
    base = (llm.api_base or _GEMINI_DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}/models"
    if llm.api_key:
        url = f"{url}?key={llm.api_key}"
    with urlopen(Request(url, method="GET"), timeout=_LLM_STATUS_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("models")
    if not isinstance(data, list):
        raise ValueError("Provider did not return a model list.")
    return tuple(
        str(item["name"]).removeprefix("models/")
        for item in data
        if isinstance(item, dict) and "name" in item
    )


def _inventory_for_provider(llm: LlmProviderDraft) -> tuple[str, ...]:
    """Dispatch model-listing to the wire protocol the provider actually speaks.
    """
    provider = llm.provider.lower()
    if provider == "anthropic":
        return _anthropic_inventory(llm)
    if provider == "gemini":
        return _gemini_inventory(llm)
    return _openai_compatible_inventory(llm)


def _uses_ollama_inventory(llm: LlmProviderDraft) -> bool:
    provider = llm.provider.lower()
    if provider == "ollama":
        return True
    api_base = llm.api_base or ""
    return "localhost:11434" in api_base or "127.0.0.1:11434" in api_base


def _ollama_model_metadata(llm: LlmProviderDraft) -> tuple[LlmModelMetadata, ...]:
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

