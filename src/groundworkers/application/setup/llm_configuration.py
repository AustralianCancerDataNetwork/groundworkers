from __future__ import annotations

from dataclasses import dataclass, replace

from oa_configurator import StackConfig  # type: ignore[import-untyped]
from pydantic import ValidationError

from groundworkers.application.setup.configuration import save_configuration
from groundworkers.application.setup.models import (
    ConfigurationSaveResult,
    ConfigurationSnapshot,
    LlmProviderCheckResult,
)
from groundworkers.application.setup.runtime_setup import verify_llm_config
from groundworkers.config import GroundworkersConfig, LLMConfig

SUPPORTED_LLM_PROVIDERS = ("ollama", "openai-compatible")
DEFAULT_PROVIDER_URLS = {
    "ollama": "http://localhost:11434/v1",
    "openai-compatible": "http://localhost:11434/v1",
}


@dataclass(frozen=True)
class LlmConfigurationDraft:
    provider: str = "ollama"
    api_base: str = "http://localhost:11434/v1"
    default_model_name: str | None = None
    api_key: str | None = None

    def safe_for_display(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "api_base": self.api_base,
            "default_model_name": self.default_model_name,
        }


@dataclass(frozen=True)
class LlmConfigurationPlan:
    path: str
    revision: str | None
    editable: bool
    read_only_reason: str | None
    draft: LlmConfigurationDraft


@dataclass(frozen=True)
class LlmConfigurationApplyResult:
    save_result: ConfigurationSaveResult
    changed_fields: tuple[str, ...]


def plan_llm_configuration(snapshot: ConfigurationSnapshot) -> LlmConfigurationPlan:
    return LlmConfigurationPlan(
        path=str(snapshot.path),
        revision=snapshot.revision,
        editable=snapshot.ownership.editable,
        read_only_reason=(
            None if snapshot.ownership.editable else snapshot.ownership.guidance
        ),
        draft=draft_from_snapshot(snapshot),
    )


def draft_from_snapshot(snapshot: ConfigurationSnapshot) -> LlmConfigurationDraft:
    if snapshot.usable and snapshot.stack is not None:
        tool = _effective_groundworkers_tool(snapshot.stack)
        if tool is not None:
            try:
                config = GroundworkersConfig.model_validate(tool)
                return LlmConfigurationDraft(
                    provider=config.llm.provider,
                    api_base=config.llm.api_base
                    or DEFAULT_PROVIDER_URLS.get(
                        config.llm.provider, "http://localhost:11434/v1"
                    ),
                    default_model_name=config.llm.default_model_name,
                    api_key=config.llm.api_key,
                )
            except (TypeError, ValueError):
                pass
    return LlmConfigurationDraft()


def update_draft(
    draft: LlmConfigurationDraft,
    **changes: object,
) -> LlmConfigurationDraft:
    coerced = dict(changes)
    if "api_base" in coerced and coerced["api_base"] is not None:
        coerced["api_base"] = str(coerced["api_base"]).strip()
    if "provider" in coerced and coerced["provider"] is not None:
        coerced["provider"] = str(coerced["provider"]).strip()
    if "default_model_name" in coerced and coerced["default_model_name"] is not None:
        model_name = str(coerced["default_model_name"]).strip()
        coerced["default_model_name"] = model_name or None
    return replace(draft, **coerced)  # type: ignore[arg-type]


def scan_llm_models(draft: LlmConfigurationDraft) -> LlmProviderCheckResult:
    return verify_llm_config(_llm_config_from_draft(draft), require_default_model=False)


def apply_llm_configuration(
    snapshot: ConfigurationSnapshot,
    draft: LlmConfigurationDraft,
) -> LlmConfigurationApplyResult:
    plan = plan_llm_configuration(snapshot)
    if not plan.editable:
        raise PermissionError(
            plan.read_only_reason
            or "This configuration cannot be edited through the setup console."
        )
    if draft.default_model_name is None:
        raise ValueError("Select a default model before saving.")

    original = snapshot.stack
    stack = _editable_stack(snapshot)
    before = stack.model_dump(mode="python")
    tool = _groundworkers_tool_with_llm(stack, draft)
    _set_effective_groundworkers_tool(stack, tool)

    try:
        StackConfig.model_validate(stack.model_dump(mode="python"))
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    result = save_configuration(
        stack,
        path=snapshot.path,
        expected_revision=snapshot.revision,
        ownership=snapshot.ownership,
    )
    return LlmConfigurationApplyResult(
        save_result=result,
        changed_fields=_changed_top_level(before, result.snapshot.stack, original),
    )


def _llm_config_from_draft(draft: LlmConfigurationDraft) -> LLMConfig:
    return LLMConfig(
        enabled=True,
        provider=draft.provider,
        api_base=draft.api_base,
        api_key=draft.api_key,
        default_model_name=draft.default_model_name,
    )


def _editable_stack(snapshot: ConfigurationSnapshot) -> StackConfig:
    if snapshot.stack is None:
        return StackConfig()
    return StackConfig.model_validate(snapshot.stack.model_dump(mode="python"))


def _effective_groundworkers_tool(stack: StackConfig) -> dict[str, object] | None:
    return stack.tools.get(GroundworkersConfig.tool_name)


def _set_effective_groundworkers_tool(
    stack: StackConfig,
    tool: dict[str, object],
) -> None:
    stack.tools[GroundworkersConfig.tool_name] = tool


def _groundworkers_tool_with_llm(
    stack: StackConfig,
    draft: LlmConfigurationDraft,
) -> dict[str, object]:
    existing = _effective_groundworkers_tool(stack)
    tool = dict(existing) if existing is not None else {}
    existing_llm = tool.get("llm", {})
    llm_extra = dict(existing_llm) if isinstance(existing_llm, dict) else {}
    llm_extra.update(
        {
            "enabled": True,
            "provider": draft.provider,
            "api_base": draft.api_base,
            "default_model_name": draft.default_model_name,
        }
    )
    if draft.api_key is not None:
        llm_extra["api_key"] = draft.api_key
    tool["llm"] = llm_extra
    GroundworkersConfig.model_validate(tool)
    return tool


def _changed_top_level(
    before: dict[str, object],
    reloaded: StackConfig | None,
    original: StackConfig | None,
) -> tuple[str, ...]:
    if reloaded is None:
        return ()
    after = reloaded.model_dump(mode="python")
    return tuple(
        key
        for key in ("tools",)
        if before.get(key) != after.get(key) or (original is None and after.get(key))
    )
