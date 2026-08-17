from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar

from oa_configurator import (
    CDMDatabaseConfig,
    ConfigurationError,
    ModelConfig,
    PackageConfigBase,
    RefTo,
    Resolver,
    StackConfig,
    VectorStoreConfig,
)
from omop_alchemy.config import OmopAlchemyConfig
from omop_emb.config import OmopEmbConfig
from omop_graph.config import OmopGraphConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.engine import Engine


class McpTransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: str = Field(default="stdio")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)


class RestTransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8080, ge=1, le=65535)
    base_path: str = Field(default="/v1")

    @field_validator("base_path")
    @classmethod
    def validate_base_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("rest.base_path must start with '/'")
        return value.rstrip("/") or "/"


class GroundingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Minimum proportion of query tokens that must appear in the matched concept
    # name for a fulltext (FTS) result to be accepted. FTS results below this
    # threshold are silently dropped; if all FTS results are dropped the tier
    # is treated as empty and grounding falls through to the embedding tier.
    min_fulltext_overlap: float = 0.0

    @field_validator("min_fulltext_overlap")
    @classmethod
    def validate_overlap(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("grounding.min_fulltext_overlap must be between 0.0 and 1.0")
        return value


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "openai-compatible"
    api_base: str | None = None
    api_key: str | None = None
    default_model_name: str | None = None

    @model_validator(mode="after")
    def validate_enabled_config(self) -> LLMConfig:
        if not self.enabled:
            return self
        if self.api_key is not None and not self.api_key.strip():
            raise ValueError("llm.api_key must be a non-empty string when provided")
        return self


class SourcePlanningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_assisted_enabled: bool = True


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    packs_root: str | None = None


class SemanticProjectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Deterministic, no LLM/DB dependency — off by default per the rollout plan
    # in agent-stack's SEMANTIC_INTEGRATION design notes: enable in local/test
    # environments first, before review/export surfaces consume it downstream.
    enabled: bool = False


class GroundworkersConfig(PackageConfigBase):
    """Package-level configuration owned by groundworkers."""

    tool_name: ClassVar[str] = "groundworkers"
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("omop_graph", "omop_emb")

    cdm_db: Annotated[str, RefTo(CDMDatabaseConfig)] = "cdm_db"
    embedding_model_name: Annotated[str | None, RefTo(ModelConfig)] = None
    vector_store_name: Annotated[str | None, RefTo(VectorStoreConfig)] = None
    app_name: str = "groundworkers"
    mcp: McpTransportConfig = Field(default_factory=McpTransportConfig)
    rest: RestTransportConfig = Field(default_factory=RestTransportConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    grounding: GroundingConfig = Field(default_factory=GroundingConfig)
    source_planning: SourcePlanningConfig = Field(default_factory=SourcePlanningConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    semantic_projection: SemanticProjectionConfig = Field(default_factory=SemanticProjectionConfig)


@dataclass(frozen=True)
class AppConfig:
    stack: StackConfig
    resolver: Resolver
    groundworkers: GroundworkersConfig
    omop_graph: OmopGraphConfig | None
    omop_emb: OmopEmbConfig | None
    cdm_resource_name: str | None
    cdm_engine: Engine | None
    emb_resource_name: str | None
    emb_engine: Engine | None
    knowledge_root: Path | None

    @property
    def app_name(self) -> str:
        return self.groundworkers.app_name

    @property
    def llm(self) -> LLMConfig:
        return self.groundworkers.llm

    @property
    def mcp(self) -> McpTransportConfig:
        return self.groundworkers.mcp

    @property
    def rest(self) -> RestTransportConfig:
        return self.groundworkers.rest

    @property
    def grounding(self) -> GroundingConfig:
        return self.groundworkers.grounding

    @property
    def source_planning(self) -> SourcePlanningConfig:
        return self.groundworkers.source_planning

    @property
    def knowledge(self) -> KnowledgeConfig:
        return self.groundworkers.knowledge

    @property
    def semantic_projection(self) -> SemanticProjectionConfig:
        return self.groundworkers.semantic_projection

    def describe(self) -> dict[str, Any]:
        llm = self.llm.model_dump(exclude_none=True)
        if llm.get("api_key"):
            llm["api_key"] = "***"

        omop_emb: dict[str, Any] | None = None
        if self.omop_emb is not None:
            omop_emb = self.omop_emb.model_dump(exclude_none=True)
            if omop_emb.get("api_key"):
                omop_emb["api_key"] = "***"
            omop_emb["resource_name"] = self.emb_resource_name

        return {
            "app_name": self.app_name,
            "stack": {
                "config_path": str(self.stack.loaded_path) if self.stack.loaded_path is not None else None,
                "active_profile": self.stack.active_profile,
            },
            "groundworkers": {
                "mcp": self.mcp.model_dump(),
                "rest": self.rest.model_dump(),
                "llm": llm,
                "grounding": self.grounding.model_dump(exclude_none=True),
                "source_planning": self.source_planning.model_dump(),
                "knowledge": {
                    **self.knowledge.model_dump(exclude_none=True),
                    "resolved_root": str(self.knowledge_root) if self.knowledge_root is not None else None,
                },
                "semantic_projection": self.semantic_projection.model_dump(),
            },
            "omop_graph": {
                "configured": self.omop_graph is not None,
                "resource_name": self.cdm_resource_name,
                "vocab_schema": self._vocab_schema(),
                "embedding_model_name": self.effective_embedding_model_name,
                "min_fulltext_overlap": self.grounding.min_fulltext_overlap,
            },
            "omop_emb": omop_emb,
        }

    @property
    def effective_embedding_model_name(self) -> str | None:
        return self.groundworkers.embedding_model_name

    def _vocab_schema(self) -> str | None:
        if self.cdm_resource_name is None:
            return None
        return self.resolver.resolve_resource(self.cdm_resource_name).vocab_schema


def has_tool_config(stack: StackConfig, tool_name: str) -> bool:
    if tool_name in stack.tools:
        return True
    if stack.active_profile and stack.active_profile in stack.profiles:
        return tool_name in stack.profiles[stack.active_profile].tools
    return False


def resolve_cdm_resource_name(stack: StackConfig) -> str:
    """Return the shared CDM resource name used by groundworkers."""

    available: set[str] = set(stack.resource_names())
    if stack.active_profile and stack.active_profile in stack.profiles:
        available |= set(stack.profiles[stack.active_profile].resources)

    seen: set[str] = set()
    candidates: list[str] = []
    for tool_name in (GroundworkersConfig.tool_name, OmopGraphConfig.tool_name, OmopAlchemyConfig.tool_name):
        tool = stack.tools.get(tool_name)
        if tool is None and stack.active_profile and stack.active_profile in stack.profiles:
            tool = stack.profiles[stack.active_profile].tools.get(tool_name)
        if tool is not None and tool.default_resource:
            candidates.append(tool.default_resource)

    candidates.append(OmopAlchemyConfig.CDM_DB.semantic_name)

    for resource_name in candidates:
        if resource_name in seen:
            continue
        seen.add(resource_name)
        resolved_name = stack.resource_aliases.get(resource_name, resource_name)
        if resolved_name in available:
            return resource_name

    alias_hint = (
        "\nTip: if you named your resource differently, add:\n"
        '  [resource_aliases]\n  cdm_db = "your-resource-name"'
    )
    raise ConfigurationError(
        "Groundworkers could not resolve a shared CDM resource. "
        f"Tried: {candidates}\n"
        f"Available: {sorted(available) or '(none)'}\n"
        "Configure 'omop_alchemy' first, or set tools.groundworkers.default_resource "
        "to the shared CDM resource name."
        + alias_hint
    )


def resolve_embedding_resource_name(stack: StackConfig) -> str:
    """Return the embedding resource name used by groundworkers."""

    available: set[str] = set(stack.resource_names())
    if stack.active_profile and stack.active_profile in stack.profiles:
        available |= set(stack.profiles[stack.active_profile].resources)

    tool = stack.tools.get(OmopEmbConfig.tool_name)
    if tool is None and stack.active_profile and stack.active_profile in stack.profiles:
        tool = stack.profiles[stack.active_profile].tools.get(OmopEmbConfig.tool_name)

    candidates = [
        tool.default_resource if tool is not None else None,
        OmopEmbConfig.EMB_DB.semantic_name,
    ]

    for resource_name in candidates:
        if not resource_name:
            continue
        resolved_name = stack.resource_aliases.get(resource_name, resource_name)
        if resolved_name in available:
            return resource_name

    alias_hint = (
        "\nTip: if you named your resource differently, add:\n"
        f'  [resource_aliases]\n  {OmopEmbConfig.EMB_DB.semantic_name} = "your-embedding-resource"'
    )
    raise ConfigurationError(
        "Groundworkers could not resolve an embedding resource for omop-emb. "
        f"Tried: {[c for c in candidates if c]}\n"
        f"Available: {sorted(available) or '(none)'}\n"
        "Run 'omop-config configure omop_emb' to provision the embedding store."
        + alias_hint
    )
