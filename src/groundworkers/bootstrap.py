from __future__ import annotations

import os
import tomllib
from pathlib import Path

from oa_configurator import Resolver, StackConfig, load_stack_config
from omop_emb.config import BackendType, OmopEmbConfig
from pydantic import ValidationError

from groundworkers.config import (
    AppConfig,
    GroundworkersConfig,
    has_tool_config,
    resolve_cdm_resource_name,
    resolve_embedding_resource_name,
)


def load_stack_config_from_path(path: str | Path) -> StackConfig:
    """Load a stack config from an explicit TOML path."""

    resolved_path = Path(path).expanduser()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Config file not found: {resolved_path}")

    try:
        data = tomllib.loads(resolved_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Malformed TOML in {resolved_path}: {exc}") from exc

    config = StackConfig.model_validate(data)
    active_profile = os.environ.get("OA_ACTIVE_PROFILE")
    if active_profile:
        config.active_profile = active_profile
    config.bind_loaded_path(resolved_path)
    return config


def build_app_config(
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> AppConfig:
    """Resolve the active stack config into a runtime AppConfig."""

    stack = load_stack_config_from_path(config_path) if config_path is not None else load_stack_config()
    if profile is not None:
        stack.active_profile = profile
    return build_app_config_from_stack(stack)


def build_app_config_from_stack(stack: StackConfig) -> AppConfig:
    """Resolve a supplied stack config into a runtime AppConfig."""

    resolver = Resolver(stack)
    groundworkers = GroundworkersConfig.from_stack(stack)

    cdm_resource_name: str | None = None
    cdm_engine = None
    omop_graph = None
    try:
        cdm_resource_name = resolve_cdm_resource_name(stack)
    except Exception:
        if has_tool_config(stack, "omop_graph"):
            raise
    else:
        cdm_engine = resolver.resolve_resource(cdm_resource_name).create_engine(future=True)
        omop_graph = GroundworkersOmopGraphConfigLoader.load(stack)

    omop_emb = None
    emb_resource_name: str | None = None
    emb_engine = None
    if has_tool_config(stack, OmopEmbConfig.tool_name):
        omop_emb = OmopEmbConfig.from_stack(stack)
        backend = BackendType(omop_emb.backend)
        if backend is BackendType.PGVECTOR:
            emb_resource_name = resolve_embedding_resource_name(stack)
            emb_engine = resolver.resolve_resource(emb_resource_name).create_engine(future=True)

    knowledge_root = None
    default_knowledge_resource = _default_knowledge_resource_name(stack)
    if default_knowledge_resource is not None:
        knowledge_root = resolver.resolve_knowledge_resource(default_knowledge_resource).root
    elif groundworkers.knowledge.packs_root is not None:
        knowledge_root = _resolve_path(groundworkers.knowledge.packs_root, stack.loaded_path)

    return AppConfig(
        stack=stack,
        resolver=resolver,
        groundworkers=groundworkers,
        omop_graph=omop_graph,
        omop_emb=omop_emb,
        cdm_resource_name=cdm_resource_name,
        cdm_engine=cdm_engine,
        emb_resource_name=emb_resource_name,
        emb_engine=emb_engine,
        knowledge_root=knowledge_root,
    )


class GroundworkersOmopGraphConfigLoader:
    """Load omop-graph config lazily without making it a hard runtime requirement."""

    @staticmethod
    def load(stack: StackConfig):
        return _load_omop_graph_config(stack)


def _load_omop_graph_config(stack: StackConfig):
    try:
        return __import__("omop_graph.config", fromlist=["OmopGraphConfig"]).OmopGraphConfig.from_stack(stack)
    except ValidationError as exc:
        raise ValueError(f"Invalid omop_graph config: {exc}") from exc


def _default_knowledge_resource_name(stack: StackConfig) -> str | None:
    tool = stack.tools.get(GroundworkersConfig.tool_name)
    if tool is None and stack.active_profile and stack.active_profile in stack.profiles:
        tool = stack.profiles[stack.active_profile].tools.get(GroundworkersConfig.tool_name)
    if tool is None:
        return None
    return getattr(tool, "default_knowledge_resource", None)


def _resolve_path(path: str, loaded_path: Path | None) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    if loaded_path is not None:
        return (loaded_path.parent / expanded).resolve()
    return expanded.resolve()
