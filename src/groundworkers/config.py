from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from oa_configurator import (  # type: ignore[import-untyped]
    CDMDatabaseConfig,
    ModelConfig,
    PackageConfigBase,
    RefTo,
    ResolvedCDMDatabase,
    ResolvedModel,
    ResolvedVectorStore,
    Resolver,
    StackConfig,
    VectorStoreConfig,
)
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
    # name for a full-text result to be accepted. If every result falls below the
    # threshold, grounding continues to the embedding tier.
    min_fulltext_overlap: float = 0.0

    @field_validator("min_fulltext_overlap")
    @classmethod
    def validate_overlap(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                "grounding.min_fulltext_overlap must be between 0.0 and 1.0"
            )
        return value


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "openai-compatible"
    api_base: str | None = Field(default=None, repr=False)
    api_key: str | None = Field(default=None, repr=False)
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

    # Deterministic and dependency-free, but opt-in while downstream review and
    # export surfaces adopt semantic projections.
    enabled: bool = False


class GroundworkersConfig(PackageConfigBase):
    """Package-level configuration owned by Groundworkers."""

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
    semantic_projection: SemanticProjectionConfig = Field(
        default_factory=SemanticProjectionConfig
    )


@dataclass(frozen=True, repr=False)
class AppConfig:
    """Resolved runtime configuration owned and consumed by Groundworkers."""

    stack: StackConfig
    resolver: Resolver
    groundworkers: GroundworkersConfig
    cdm_database: ResolvedCDMDatabase
    cdm_engine: Engine
    vocabulary_engine: Engine
    embedding_model: ResolvedModel | None
    vector_store: ResolvedVectorStore | None
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

    @property
    def effective_embedding_model_name(self) -> str | None:
        return self.embedding_model.model if self.embedding_model is not None else None

    def describe(self) -> dict[str, Any]:
        """Return an operator-safe description without credentials or raw URLs."""

        llm = self.llm.model_dump(exclude_none=True)
        if llm.get("api_key"):
            llm["api_key"] = "***"
        if isinstance(llm.get("api_base"), str):
            llm["api_base"] = _safe_endpoint(llm["api_base"])

        model = None
        if self.embedding_model is not None:
            resolved = self.embedding_model
            model = {
                "name": resolved.name,
                "provider": {
                    "name": resolved.provider.name,
                    "provider": resolved.provider.provider,
                    "base_url": _safe_endpoint(resolved.provider.base_url),
                    "api_key": "***" if resolved.provider.api_key else None,
                },
                "model": resolved.model,
                "embedding_dim": resolved.embedding_dim,
                "embeddings": resolved.embeddings,
            }

        vector_store = None
        if self.vector_store is not None:
            resolved_store = self.vector_store
            vector_store = {
                "name": resolved_store.name,
                "backend_type": resolved_store.backend_type,
                "database": {
                    "name": resolved_store.database.name,
                    "connection": resolved_store.database.connection.name,
                    "safe_url": _safe_endpoint(
                        resolved_store.database.connection.safe_url
                    ),
                    "schema_name": resolved_store.database.schema_name,
                },
                "faiss_cache_dir": resolved_store.faiss_cache_dir,
            }

        database = self.cdm_database
        return {
            "app_name": self.app_name,
            "stack": {
                "config_path": str(self.stack.loaded_path)
                if self.stack.loaded_path is not None
                else None,
            },
            "groundworkers": {
                "mcp": self.mcp.model_dump(),
                "rest": self.rest.model_dump(),
                "llm": llm,
                "grounding": self.grounding.model_dump(exclude_none=True),
                "source_planning": self.source_planning.model_dump(),
                "knowledge": {
                    **self.knowledge.model_dump(exclude_none=True),
                    "resolved_root": str(self.knowledge_root)
                    if self.knowledge_root is not None
                    else None,
                },
                "semantic_projection": self.semantic_projection.model_dump(),
            },
            "database": {
                "name": database.name,
                "connection": database.connection.name,
                "safe_url": _safe_endpoint(database.connection.safe_url),
                "schema_name": database.schema_name,
                "vocabulary_connection": database.vocab_connection.name,
                "vocabulary_safe_url": _safe_endpoint(
                    database.vocab_connection.safe_url
                ),
                "vocabulary_schema": database.vocab_schema,
                "results_schema": database.results_schema,
            },
            "model": model,
            "vector_store": vector_store,
        }

    def __repr__(self) -> str:
        return f"AppConfig({self.describe()!r})"


_SENSITIVE_QUERY_PARTS = ("key", "token", "secret", "password", "credential")


def _safe_endpoint(value: str | None) -> str | None:
    if value is None:
        return None
    parts = urlsplit(value)
    hostname = parts.hostname or ""
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    query = urlencode(
        [
            (
                key,
                "***"
                if any(part in key.lower() for part in _SENSITIVE_QUERY_PARTS)
                else item,
            )
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
