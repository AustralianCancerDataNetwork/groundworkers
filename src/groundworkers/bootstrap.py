from __future__ import annotations

from pathlib import Path

from oa_configurator import (  # type: ignore[import-untyped]
    ResolvedCDMDatabase,
    Resolver,
    StackConfig,
    load_stack_config,
    load_stack_config_from_path,
)

from groundworkers.config import AppConfig, GroundworkersConfig


def build_app_config(*, config_path: str | Path | None = None) -> AppConfig:
    """Resolve the stack configuration into Groundworkers' runtime view."""

    stack = (
        load_stack_config_from_path(config_path)
        if config_path is not None
        else load_stack_config()
    )
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

    cdm_engine = cdm_database.connection.create_engine(future=True)
    if cdm_database.vocab_connection.name == cdm_database.connection.name:
        vocabulary_engine = cdm_engine
    else:
        vocabulary_engine = cdm_database.vocab_connection.create_engine(future=True)

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
        vocabulary_engine=vocabulary_engine,
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
