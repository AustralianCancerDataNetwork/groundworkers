from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar

from oa_configurator import (  # type: ignore[import-untyped]
    CDMDatabaseConfig,
    ModelConfig,
    PackageConfigBase,
    RefTo,
    ResolvedCDMDatabase,
    ResolvedModel,
    ResolvedVectorStore,
    StackConfig,
    VectorStoreConfig,
    safe_endpoint,
)
from pydantic import Field, field_validator
from sqlalchemy.engine import Engine


class GroundworkersConfig(PackageConfigBase):
    """Package-level configuration owned by Groundworkers.

    The shared configure path (``omop-config configure groundworkers``, and 
    the ``--set`` flags it generates) addresses one field at a time. Related 
    settings are grouped by name prefix.

    Every field carries a ``description``, which provides the CLI flag help, the
    interactive prompt text, and the generated documentation.

    Notes
    -----
    By design, this config is for internal use only and must not be imported or
    resolved by any other package.
    """

    tool_name: ClassVar[str] = "groundworkers"
    extra_logging_namespaces: ClassVar[tuple[str, ...]] = ("omop_graph", "omop_emb")

    # -- References into the shared stack -----------------------------------

    cdm_db: Annotated[str, RefTo(CDMDatabaseConfig)] = Field(
        default="cdm_db",
        description=(
            "Name of the [databases.*] entry holding the CDM database."
        ),
    )
    embedding_model_name: Annotated[str | None, RefTo(ModelConfig)] = Field(
        default=None,
        description=(
            "Name of a [models.*] entry used to embed query text. Optional."
        ),
    )
    llm_model_name: Annotated[str | None, RefTo(ModelConfig)] = Field(
        default=None,
        description=(
            "Name of a [models.*] entry used for chat. Never the same entry as "
            "embedding_model_name, and free to sit on a different provider. "
            "Unset means text, domain, and LLM-assisted source planning are off. "
            "There is no separate enabled flag."
        ),
    )
    vector_store_name: Annotated[str | None, RefTo(VectorStoreConfig)] = Field(
        default=None,
        description=(
            "Name of a [vector_stores.*] entry holding concept embeddings. "
            "Required alongside embedding_model_name for the embedding tier."
        ),
    )

    # -- Identity ------------------------------------------------------------

    app_name: str = Field(
        default="groundworkers",
        description="Server name reported to MCP clients.",
    )

    # -- MCP transport -------------------------------------------------------

    mcp_transport: str = Field(
        default="stdio",
        description=(
            "Default MCP transport: stdio, sse, or streamable-http. The CLI also "
            "accepts 'rest', which serves the REST API instead of MCP."
        ),
    )
    mcp_host: str = Field(
        default="127.0.0.1",
        description="Bind host for the HTTP-based MCP transports. Ignored by stdio.",
    )
    mcp_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Bind port for the HTTP-based MCP transports. Ignored by stdio.",
    )

    # -- REST transport ------------------------------------------------------

    # REST is selected explicitly with ``--transport rest``.  There is no
    # alongside-MCP enable flag: keeping one would imply startup behaviour the
    # CLI does not implement.
    rest_host: str = Field(
        default="127.0.0.1",
        description="Bind host for the REST API.",
    )
    rest_port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        description="Bind port for the REST API.",
    )
    rest_base_path: str = Field(
        default="/v1",
        description="Path prefix every REST route is mounted under. Must start with '/'.",
    )

    # -- Grounding policy ----------------------------------------------------

    grounding_min_fulltext_overlap: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum proportion of query tokens that must appear in the matched "
            "concept name for a full-text result to be accepted. If every result "
            "falls below the threshold, grounding continues to the embedding tier."
        ),
    )
    grounding_max_depth: int = Field(
        default=5,
        ge=1,
        le=10,
        description=(
            "Maximum hierarchy distance between a grounding candidate and a "
            "required parent concept, or maximum identity-hop count when "
            "grounding runs without parent_ids. Owned by Groundworkers: "
            "omop-graph's own traversal limits are per-call arguments, not "
            "shared configuration."
        ),
    )

    # -- Source planning -----------------------------------------------------

    source_planning_llm_assisted_enabled: bool = Field(
        default=True,
        description=(
            "Whether source planning may consult the chat model to classify "
            "ambiguous columns. No effect unless llm_model_name is set."
        ),
    )

    # -- Knowledge packs -----------------------------------------------------

    knowledge_packs_root: str | None = Field(
        default=None,
        description=(
            "Directory holding knowledge packs. Relative paths resolve against "
            "the config file's own location. Unset uses the packs shipped in the "
            "installed package."
        ),
    )

    # -- Semantic projection -------------------------------------------------

    semantic_projection_enabled: bool = Field(
        default=False,
        description=(
            "Whether the semantic projection tools are registered. Deterministic "
            "and dependency-free, but opt-in while downstream review and export "
            "surfaces adopt semantic projections."
        ),
    )

    @field_validator("rest_base_path")
    @classmethod
    def validate_rest_base_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("rest_base_path must start with '/'")
        return value.rstrip("/") or "/"


def split_vocabulary_connection(
    stack: StackConfig, cdm_db: str
) -> tuple[str, str] | None:
    """Report a CDM entry whose vocabulary sits on a second physical connection.

    Groundworkers reads the vocabulary through the CDM engine and nothing else:
    the knowledge graph, the vocabulary service, and the embedding tier are all
    handed ``AppConfig.cdm_engine``, with only the vocabulary *schema* applied on
    top. A ``vocab_connection`` naming a different server is therefore not a
    supported split -- it would be silently ignored and the vocabulary schema
    looked for on the CDM server instead. Reported here so both the runtime and
    the setup console can refuse it in the same terms, rather than plumbing a
    second engine no caller reads.

    Returns
    -------
    tuple[str, str] or None
        The ``(primary, vocabulary)`` connection names when they differ, or
        ``None`` when the entry is absent, is not a CDM entry, or names one
        connection for both roles.
    """

    database = stack.databases.get(cdm_db)
    if not isinstance(database, CDMDatabaseConfig):
        return None
    vocabulary = database.vocab_connection
    if vocabulary is None or vocabulary == database.connection:
        return None
    return (database.connection, vocabulary)


@dataclass(frozen=True, repr=False)
class AppConfig:
    """Resolved runtime configuration owned and consumed by Groundworkers.

    Groundworkers is the only application in the stack rather than a library, so
    unlike its siblings it resolves the whole picture once and hands the same
    object to the tool registry, both transports, ``--describe``, and the setup
    console. Each optional backend is resolved to ``None`` when unconfigured, so
    availability is decided in one place instead of at every call site.

    Settings are read through ``groundworkers``: this type deliberately does not
    re-export them, so each one has exactly one name.

    There is one database engine, not two. The vocabulary is reached through
    ``cdm_engine`` with ``cdm_database.vocab_schema`` applied on top, so a CDM
    entry that splits its vocabulary onto a second connection is refused at
    bootstrap rather than represented here. See
    :func:`split_vocabulary_connection`.
    """

    stack: StackConfig
    groundworkers: GroundworkersConfig
    cdm_database: ResolvedCDMDatabase
    cdm_engine: Engine
    embedding_model: ResolvedModel | None
    llm_model: ResolvedModel | None
    vector_store: ResolvedVectorStore | None
    knowledge_root: Path | None

    def describe(self) -> dict[str, Any]:
        """Return an operator-safe description without credentials or raw URLs."""

        vector_store = None
        if self.vector_store is not None:
            resolved_store = self.vector_store
            vector_store = {
                "name": resolved_store.name,
                "backend_type": resolved_store.backend_type,
                "database": {
                    "name": resolved_store.database.name,
                    "connection": resolved_store.database.connection.name,
                    "safe_url": safe_endpoint(
                        resolved_store.database.connection.safe_url
                    ),
                    "schema_name": resolved_store.database.schema_name,
                },
                "faiss_cache_dir": resolved_store.faiss_cache_dir,
            }

        database = self.cdm_database
        return {
            "app_name": self.groundworkers.app_name,
            "stack": {
                "config_path": str(self.stack.loaded_path)
                if self.stack.loaded_path is not None
                else None,
            },
            "groundworkers": {
                **self.groundworkers.model_dump(),
                "knowledge_resolved_root": str(self.knowledge_root)
                if self.knowledge_root is not None
                else None,
            },
            "database": {
                "name": database.name,
                "connection": database.connection.name,
                "safe_url": safe_endpoint(database.connection.safe_url),
                "schema_name": database.schema_name,
                "vocabulary_connection": database.vocab_connection.name,
                "vocabulary_safe_url": safe_endpoint(
                    database.vocab_connection.safe_url
                ),
                "vocabulary_schema": database.vocab_schema,
                "results_schema": database.results_schema,
            },
            "model": _describe_model(self.embedding_model),
            "llm_model": _describe_model(self.llm_model),
            "vector_store": vector_store,
        }

    def __repr__(self) -> str:
        return f"AppConfig({self.describe()!r})"


def _describe_model(resolved: ResolvedModel | None) -> dict[str, Any] | None:
    """Operator-safe view of a resolved [models.*] entry.

    Used for both the embedding and chat models: they are the same kind of
    entry, so they get the same redacted shape.
    """
    if resolved is None:
        return None
    return {
        "name": resolved.name,
        "provider": {
            "name": resolved.provider.name,
            "provider": resolved.provider.provider,
            "base_url": safe_endpoint(resolved.provider.base_url),
            "api_key_configured": bool(resolved.provider.api_key),
        },
        "model": resolved.model,
        "embedding_dim": resolved.embedding_dim,
        "embeddings": resolved.embeddings,
        "structured_output": resolved.structured_output,
        "tool_use": resolved.tool_use,
    }
