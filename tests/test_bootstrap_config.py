from __future__ import annotations

from pathlib import Path

import pytest
from oa_configurator import CDMDatabaseConfig, ConfigurationError, ConnectionConfig
from oa_configurator.io import save_stack_config

from groundworkers.bootstrap import (
    build_app_config_from_stack,
    load_stack_config_from_path,
)
from groundworkers.config import GroundworkersConfig
from tests.support.stack_config import (
    build_cdm_stack,
    build_embedding_stack,
    build_invalid_reference_stack,
)


def test_groundworkers_config_reads_plain_package_mapping() -> None:
    stack = build_cdm_stack(
        groundworkers={
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
            "grounding": {"min_fulltext_overlap": 0.25},
            "source_planning": {"llm_assisted_enabled": False},
            "knowledge": {"packs_root": "knowledge/packs"},
        }
    )

    config = GroundworkersConfig.validate_candidate(stack)

    assert config.app_name == "groundworkers-test"
    assert config.mcp.transport == "streamable-http"
    assert config.mcp.host == "0.0.0.0"
    assert config.mcp.port == 18080
    assert config.rest.enabled is True
    assert config.rest.base_path == "/api"
    assert config.llm.enabled is True
    assert config.llm.default_model_name == "gpt-test"
    assert config.grounding.min_fulltext_overlap == 0.25
    assert config.source_planning.llm_assisted_enabled is False
    assert config.knowledge.packs_root == "knowledge/packs"


def test_load_stack_config_from_path_binds_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), config_path)

    stack = load_stack_config_from_path(config_path)

    assert stack.loaded_path == config_path


def test_load_stack_config_from_path_rejects_malformed_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        "[tools.groundworkers\napp_name = 'broken'\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Malformed TOML"):
        load_stack_config_from_path(config_path)


def test_load_stack_config_validation_error_does_not_echo_rejected_secret(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"
api_key = "rejected-secret"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as raised:
        load_stack_config_from_path(config_path)

    assert "rejected-secret" not in str(raised.value)


def test_build_app_config_resolves_relative_packs_root(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_stack_config(
        build_cdm_stack(groundworkers={"knowledge": {"packs_root": "relative-packs"}}),
        config_path,
    )

    config = build_app_config_from_stack(load_stack_config_from_path(config_path))

    assert config.knowledge_root == (tmp_path / "relative-packs").resolve()


def test_cdm_only_runtime_resolves_without_embedding_configuration() -> None:
    config = build_app_config_from_stack(build_cdm_stack())

    assert config.cdm_database.name == "cdm_db"
    assert config.embedding_model is None
    assert config.vector_store is None
    assert config.vocabulary_engine is config.cdm_engine


def test_distinct_vocabulary_connection_gets_its_own_engine() -> None:
    stack = build_cdm_stack()
    stack.connections["vocab_main"] = ConnectionConfig(
        dialect="sqlite",
        database_name="vocabulary.db",
    )
    stack.databases["cdm_db"] = CDMDatabaseConfig(
        connection="cdm_main",
        schema_name="main",
        vocab_connection="vocab_main",
        vocab_schema="main",
    )

    config = build_app_config_from_stack(stack)

    assert config.cdm_database.vocab_connection.name == "vocab_main"
    assert config.vocabulary_engine is not config.cdm_engine


def test_optional_model_and_vector_store_are_resolved_without_building_backends() -> (
    None
):
    config = build_app_config_from_stack(build_embedding_stack())

    assert config.embedding_model is not None
    assert config.embedding_model.name == "embedding_model"
    assert config.vector_store is not None
    assert config.vector_store.name == "embedding_store"


@pytest.mark.parametrize("issue", ["missing_cdm", "wrong_cdm_kind"])
def test_invalid_cdm_reference_is_rejected_at_bootstrap(issue: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        build_app_config_from_stack(build_invalid_reference_stack(issue))  # type: ignore[arg-type]


def test_describe_masks_connection_and_provider_secrets() -> None:
    stack = build_embedding_stack()
    stack.tools["groundworkers"]["llm"] = {
        "enabled": True,
        "api_base": "https://chat.example.test/v1?api_key=llm-query-secret",
        "api_key": "llm-secret",
    }
    stack.connections["cdm_main"] = ConnectionConfig(
        dialect="postgresql+psycopg",
        host="database.example.test",
        user="analyst",
        password="database-secret",
        database_name="omop",
    )
    provider = stack.providers["embedding_provider"]
    provider.api_key = "provider-secret"
    provider.base_url = (
        "https://user:pass@models.example.test/v1?api_key=query-secret#fragment-secret"
    )

    config = build_app_config_from_stack(stack)
    rendered = repr(config.describe())
    runtime_repr = repr(config)
    package_repr = repr(config.groundworkers)

    assert "database-secret" not in rendered + runtime_repr
    assert "provider-secret" not in rendered + runtime_repr
    assert "query-secret" not in rendered + runtime_repr
    assert "fragment-secret" not in rendered + runtime_repr
    assert "user:pass" not in rendered + runtime_repr
    assert "llm-secret" not in package_repr
    assert "llm-query-secret" not in package_repr
    assert "***" in rendered
