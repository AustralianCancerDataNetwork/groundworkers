from __future__ import annotations

import tomllib
from pathlib import Path

from oa_configurator import (  # type: ignore[import-untyped]
    ConfigurationError,
    ResolvedCDMDatabase,
    Resolver,
    StackConfig,
    load_stack_config,
)
from pydantic import ValidationError

from groundworkers.config import AppConfig, GroundworkersConfig


def load_stack_config_from_path(path: str | Path) -> StackConfig:
    """Load a stack config from an explicit TOML path."""

    resolved_path = Path(path).expanduser()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Config file not found: {resolved_path}")

    try:
        data = tomllib.loads(resolved_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Malformed TOML in {resolved_path}: {exc}") from exc

    try:
        config = StackConfig.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(
                include_input=False,
                include_url=False,
                include_context=False,
            )
        )
        raise ConfigurationError(
            f"Invalid stack configuration in {resolved_path}: {problems}"
        ) from None
    config.bind_loaded_path(resolved_path)
    return config


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
    vector_store = (
        resolver.resolve_vector_store(groundworkers.vector_store_name)
        if groundworkers.vector_store_name is not None
        else None
    )

    knowledge_root = None
    if groundworkers.knowledge.packs_root is not None:
        knowledge_root = _resolve_path(
            groundworkers.knowledge.packs_root,
            stack.loaded_path,
        )

    return AppConfig(
        stack=stack,
        resolver=resolver,
        groundworkers=groundworkers,
        cdm_database=cdm_database,
        cdm_engine=cdm_engine,
        vocabulary_engine=vocabulary_engine,
        embedding_model=embedding_model,
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
