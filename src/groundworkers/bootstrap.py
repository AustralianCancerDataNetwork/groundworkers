from __future__ import annotations

from pathlib import Path

from oa_configurator import (  # type: ignore[import-untyped]
    ConfigurationError,
    ResolvedCDMDatabase,
    Resolver,
    Role,
    StackConfig,
    load_stack_config,
)
from omop_alchemy.config import create_cdm_engines
from omop_alchemy.cross_database import cdm_sessionmaker

from groundworkers.config import (
    AppConfig,
    GroundworkersConfig,
)


class DatabaseUnavailableError(ConfigurationError):
    """A valid stack whose configured CDM endpoints cannot be initialized."""


def build_app_config(*, config_path: str | Path | None = None) -> AppConfig:
    """Resolve the stack configuration into Groundworkers' runtime view."""

    stack = load_stack_config(config_path)
    return build_app_config_from_stack(stack)


def build_app_config_from_stack(stack: StackConfig) -> AppConfig:
    """Resolve a supplied stack without constructing embedding runtime objects."""

    groundworkers = GroundworkersConfig.validate_candidate(stack)
    resolver = Resolver(stack)
    cdm_database = resolver.resolve_database(groundworkers.cdm_db)
    if not isinstance(cdm_database, ResolvedCDMDatabase):
        raise TypeError(
            f"Groundworkers cdm_db {groundworkers.cdm_db!r} must reference a CDM database."
        )
    try:
        cdm_engine, cdm_vocab_engine = create_cdm_engines(
            cdm_database, register_claims=False
        )
    except Exception as exc:
        detail = " ".join(str(exc).splitlines())
        for role in (Role.PRIMARY, Role.VOCAB):
            connection = cdm_database.connection_for_role(role)
            detail = detail.replace(connection.url, connection.safe_url)
        primary = cdm_database.connection_for_role(Role.PRIMARY)
        vocab = cdm_database.connection_for_role(Role.VOCAB)
        endpoints = (
            "the configured CDM database endpoint"
            if primary.url == vocab.url
            else "the primary and vocabulary database endpoints"
        )
        raise DatabaseUnavailableError(
            f"Could not initialize Groundworkers CDM database {cdm_database.name!r}. "
            f"Check {endpoints}, credentials, configured schemas, and user access. "
            f"({type(exc).__name__}: {detail})"
        ) from exc
    cdm_session_factory = cdm_sessionmaker(
        cdm_database,
        primary=cdm_engine,
        vocab=cdm_vocab_engine,
    )

    embedding_model = (
        resolver.resolve_model(groundworkers.embedding_model_name)
        if groundworkers.embedding_model_name is not None
        else None
    )
    llm_model = (
        resolver.resolve_model(groundworkers.llm_model_name)
        if groundworkers.llm_model_name is not None
        else None
    )
    vector_store = (
        resolver.resolve_vector_store(groundworkers.vector_store_name)
        if groundworkers.vector_store_name is not None
        else None
    )

    knowledge_root = None
    if groundworkers.knowledge_packs_root is not None:
        knowledge_root = _resolve_path(
            groundworkers.knowledge_packs_root,
            stack.loaded_path,
        )

    return AppConfig(
        stack=stack,
        groundworkers=groundworkers,
        cdm_database=cdm_database,
        cdm_engine=cdm_engine,
        cdm_vocab_engine=cdm_vocab_engine,
        cdm_session_factory=cdm_session_factory,
        embedding_model=embedding_model,
        llm_model=llm_model,
        vector_store=vector_store,
        knowledge_root=knowledge_root,
    )


def _resolve_path(path: str, loaded_path: Path | None) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    if loaded_path is not None:
        return (loaded_path.parent / expanded).resolve()
    return expanded.resolve()
