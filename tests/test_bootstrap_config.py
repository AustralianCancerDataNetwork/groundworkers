from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
from oa_configurator import (
    DatabaseConfig,
    ResourceConfig,
    StackConfig,
    ToolConfig,
)
from oa_configurator.io import save_stack_config

from groundworkers.bootstrap import build_app_config_from_stack, load_stack_config_from_path
from groundworkers.config import GroundworkersConfig


def test_groundworkers_config_reads_typed_package_extras() -> None:
    stack = StackConfig.for_session(
        tools={
            "groundworkers": ToolConfig(
                extra={
                    "app_name": "groundworkers-test",
                    "mcp": {
                        "transport": "streamable-http",
                        "host": "0.0.0.0",
                        "port": 18080,
                    },
                    "rest": {
                        "enabled": True,
                        "host": "127.0.0.1",
                        "port": 18181,
                        "base_path": "/api/",
                    },
                    "llm": {
                        "enabled": True,
                        "provider": "openai-compatible",
                        "api_base": "http://llm.example.test/v1",
                        "default_model_name": "gpt-test",
                    },
                    "grounding": {
                        "embedding_model_name": "bge-small-en-v1.5",
                        "min_fulltext_overlap": 0.25,
                    },
                    "source_planning": {
                        "llm_assisted_enabled": False,
                    },
                    "knowledge": {
                        "packs_root": "knowledge/packs",
                    },
                }
            )
        }
    )

    config = GroundworkersConfig.from_stack(stack)

    assert config.app_name == "groundworkers-test"
    assert config.mcp.transport == "streamable-http"
    assert config.mcp.host == "0.0.0.0"
    assert config.mcp.port == 18080
    assert config.rest.enabled is True
    assert config.rest.base_path == "/api"
    assert config.llm.enabled is True
    assert config.llm.default_model_name == "gpt-test"
    assert config.grounding.embedding_model_name == "bge-small-en-v1.5"
    assert config.grounding.min_fulltext_overlap == 0.25
    assert config.source_planning.llm_assisted_enabled is False
    assert config.knowledge.packs_root == "knowledge/packs"


def test_load_stack_config_from_path_binds_path_and_respects_env_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    save_stack_config(
        StackConfig.for_session(
            active_profile="prod",
            databases={"db": DatabaseConfig(dialect="sqlite", database_name=":memory:")},
            resources={"cdm_db": ResourceConfig(database="db", cdm_schema="omop")},
        ),
        config_path,
    )

    monkeypatch.setenv("OA_ACTIVE_PROFILE", "test")

    stack = load_stack_config_from_path(config_path)

    assert stack.loaded_path == config_path
    assert stack.active_profile == "test"


def test_load_stack_config_from_path_rejects_malformed_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[tools.groundworkers.extra\napp_name = 'broken'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Malformed TOML"):
        load_stack_config_from_path(config_path)


def test_build_app_config_from_stack_resolves_relative_packs_root_from_loaded_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_stack_config(
        StackConfig.for_session(
            tools={
                "groundworkers": ToolConfig(
                    extra={
                        "knowledge": {
                            "packs_root": "relative-packs",
                        }
                    }
                )
            }
        ),
        config_path,
    )
    stack = load_stack_config_from_path(config_path)

    config = build_app_config_from_stack(stack)

    assert config.knowledge_root == (tmp_path / "relative-packs").resolve()


def test_build_app_config_from_stack_leaves_knowledge_root_unset_without_configuration() -> None:
    config = build_app_config_from_stack(StackConfig.for_session())

    assert config.knowledge_root is None
