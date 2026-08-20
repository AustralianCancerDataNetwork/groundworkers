from __future__ import annotations

import logging
from pathlib import Path

import pytest
from oa_configurator import (
    CDMDatabaseConfig,
    ConfigurationError,
    ConnectionConfig,
    load_stack_config_from_path,
)
from oa_configurator.io import save_stack_config

from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.config import GroundworkersConfig
from tests.support.stack_config import (
    add_chat_model,
    build_cdm_stack,
    build_embedding_stack,
    build_invalid_reference_stack,
)


def test_groundworkers_config_reads_plain_package_mapping() -> None:
    stack = build_cdm_stack(
        groundworkers={
            "app_name": "groundworkers-test",
            "mcp_transport": "streamable-http",
            "mcp_host": "0.0.0.0",
            "mcp_port": 18080,
            "rest_enabled": True,
            "rest_host": "127.0.0.1",
            "rest_port": 18181,
            "rest_base_path": "/api/",
            "grounding_min_fulltext_overlap": 0.25,
            "source_planning_llm_assisted_enabled": False,
            "knowledge_packs_root": "knowledge/packs",
        }
    )

    config = GroundworkersConfig.validate_candidate(stack)

    assert config.app_name == "groundworkers-test"
    assert config.mcp_transport == "streamable-http"
    assert config.mcp_host == "0.0.0.0"
    assert config.mcp_port == 18080
    assert config.rest_enabled is True
    assert config.rest_base_path == "/api"
    assert config.llm_model_name is None
    assert config.grounding_min_fulltext_overlap == 0.25
    assert config.source_planning_llm_assisted_enabled is False
    assert config.knowledge_packs_root == "knowledge/packs"


def test_config_loading_contract_relied_on_by_bootstrap(
    tmp_path: Path,
    caplog,
) -> None:
    """Pins the oa-configurator guarantees `build_app_config` depends on.

    Path loading is upstream's since it was made public; Groundworkers no longer
    has a copy. This asserts the boundary rather than the implementation,
    because a `--config-path` load is the first thing that runs and the file
    holds database passwords.
    """
    config_path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), config_path)
    config_path.chmod(0o644)

    with caplog.at_level(logging.WARNING, logger="oa_configurator.logging_config"):
        stack = load_stack_config_from_path(config_path)

    assert stack.loaded_path == config_path
    # Gained by adopting upstream: the local copy never warned about a config
    # file other users can read.
    assert any("loose permissions" in record.message for record in caplog.records)


def test_malformed_config_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        "[tools.groundworkers\napp_name = 'broken'\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Malformed TOML"):
        load_stack_config_from_path(config_path)


def test_config_validation_error_does_not_echo_rejected_secret(
    tmp_path: Path,
) -> None:
    """A rejected value must not be echoed back: it may be the secret itself."""
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
        build_cdm_stack(groundworkers={"knowledge_packs_root": "relative-packs"}),
        config_path,
    )

    config = build_app_config_from_stack(load_stack_config_from_path(config_path))

    assert config.knowledge_root == (tmp_path / "relative-packs").resolve()


def test_cdm_only_runtime_resolves_without_embedding_configuration() -> None:
    config = build_app_config_from_stack(build_cdm_stack())

    assert config.cdm_database.name == "cdm_db"
    assert config.embedding_model is None
    assert config.vector_store is None


def test_a_vocabulary_connection_naming_the_cdm_connection_is_accepted() -> None:
    """Spelling out the same connection is redundant, not a split."""
    stack = build_cdm_stack()
    stack.databases["cdm_db"] = CDMDatabaseConfig(
        connection="cdm_main",
        schema_name="main",
        vocab_connection="cdm_main",
        vocab_schema="vocab",
    )

    config = build_app_config_from_stack(stack)

    assert config.cdm_database.vocab_schema == "vocab"


def test_distinct_vocabulary_connection_is_refused() -> None:
    """The graph, the vocabulary service, and the embedding tier all read the CDM
    engine; a second connection would be silently ignored."""
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

    with pytest.raises(ConfigurationError, match="vocab_schema"):
        build_app_config_from_stack(stack)


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
    add_chat_model(
        stack,
        base_url="https://chat.example.test/v1?api_key=llm-query-secret",
        api_key="llm-secret",
    )
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
    assert "llm-secret" not in rendered + runtime_repr + package_repr
    assert "llm-query-secret" not in rendered + runtime_repr + package_repr
    assert "***" in rendered


def test_the_shipped_example_config_loads() -> None:

    example = Path(__file__).resolve().parents[1] / "config" / "groundworkers.example.toml"

    config = GroundworkersConfig.validate_candidate(load_stack_config_from_path(example))

    assert config.cdm_db == "cdm_db"
    assert config.embedding_model_name == "embedding_model"
    assert config.vector_store_name == "embeddings"


def test_endpoint_redaction_contract_relied_on_by_describe() -> None:
    """Pins the oa-configurator guarantees `describe()` depends on.

    Redaction is entirely upstream's -- Groundworkers holds no secret word list
    and no copy of this logic. This asserts the boundary rather than the
    implementation, because the behaviour has moved once already and `describe()`
    output reaches an agent.
    """
    from oa_configurator import safe_endpoint

    # Every query value is masked, whatever the parameter is called, and the
    # keys survive so an operator can still see what is set.
    masked = safe_endpoint("https://api.example.test/v1?api_key=leaked&api-version=2024-02-01")
    assert "leaked" not in masked
    assert "2024-02-01" not in masked
    assert "api_key" in masked and "api-version" in masked

    # Username survives (diagnostic, not secret); password does not.
    assert safe_endpoint("https://user:pw@api.example.test/v1") == "https://user:***@api.example.test/v1"

    # Fragments are masked rather than dropped, so a token cannot ride along.
    assert safe_endpoint("https://api.example.test/v1#tok=leaked") == "https://api.example.test/v1#***"

    assert safe_endpoint(None) is None


def test_describe_leaks_no_value_declared_sensitive_by_the_schema() -> None:
    """Trust the Sensitive() declaration; verify the pipeline honours it.

    Asserts no field oa-configurator marks Sensitive() reaches `describe()`,
    without this test having any opinion about which field names are secrets.
    """
    from oa_configurator import assert_no_sensitive_values_leak

    stack = build_embedding_stack()
    add_chat_model(stack, api_key="chat-secret")
    stack.providers["embedding_provider"].api_key = "embedding-secret"
    stack.connections["cdm_main"] = ConnectionConfig(
        dialect="postgresql+psycopg",
        host="database.example.test",
        user="analyst",
        password="database-secret",
        database_name="omop",
    )

    rendered = build_app_config_from_stack(stack).describe()

    for provider in stack.providers.values():
        assert_no_sensitive_values_leak(provider, rendered)
    for connection in stack.connections.values():
        assert_no_sensitive_values_leak(connection, rendered)
