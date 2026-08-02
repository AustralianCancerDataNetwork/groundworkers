from __future__ import annotations

from oa_configurator import StackConfig
from omop_graph.config import OmopGraphConfig

from groundworkers.application.setup.embedding_setup import safe_api_base
from groundworkers.application.setup.models import (
    ChatConfiguration,
    ConfigurationSnapshot,
    GraphConfiguration,
    LlmProviderConfiguration,
)
from groundworkers.config import (
    GroundworkersConfig,
    has_tool_config,
    resolve_cdm_resource_name,
)


def load_graph_configuration(
    snapshot: ConfigurationSnapshot,
) -> GraphConfiguration | None:
    """Resolve graph settings without constructing a graph adapter."""

    if not snapshot.usable or snapshot.stack is None:
        return None
    if not has_tool_config(snapshot.stack, OmopGraphConfig.tool_name):
        return None
    try:
        tool = _effective_tool(snapshot.stack, OmopGraphConfig.tool_name)
        config = OmopGraphConfig.model_validate(tool.extra if tool else {})
        resource_name = resolve_cdm_resource_name(snapshot.stack)
    except (KeyError, TypeError, ValueError):
        return None
    return GraphConfiguration(
        resource_name=resource_name,
        max_depth=config.max_depth,
        max_paths=config.max_paths,
    )


def load_llm_provider_configuration(
    snapshot: ConfigurationSnapshot,
) -> LlmProviderConfiguration | None:
    """Resolve the provider endpoint and defaults without exposing credentials."""

    if not snapshot.usable or snapshot.stack is None:
        return None
    if not has_tool_config(snapshot.stack, GroundworkersConfig.tool_name):
        return None
    try:
        tool = _effective_tool(snapshot.stack, GroundworkersConfig.tool_name)
        config = GroundworkersConfig.model_validate(tool.extra if tool else {})
        llm = config.llm
    except (KeyError, TypeError, ValueError):
        return None
    return LlmProviderConfiguration(
        enabled=llm.enabled,
        provider=llm.provider,
        api_base=safe_api_base(llm.api_base) if llm.api_base else None,
        credentials_configured=bool(llm.api_key),
        default_model_name=llm.default_model_name,
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


def _effective_tool(stack: StackConfig, name: str):
    if stack.active_profile and stack.active_profile in stack.profiles:
        profile_tool = stack.profiles[stack.active_profile].tools.get(name)
        if profile_tool is not None:
            return profile_tool
    return stack.tools.get(name)
