from __future__ import annotations

from pathlib import Path

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
)


def test_runtime_sections_load_from_the_effective_profile_and_redact_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
active_profile = "local"

[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"

[profiles.local.tools.omop_graph.extra]
max_depth = 4
max_paths = 8

[profiles.local.tools.groundworkers.extra.llm]
enabled = true
provider = "openai-compatible"
api_base = "https://provider.example/v1?api_key=secret"
api_key = "also-secret"
default_model_name = "chat-model"
""",
        encoding="utf-8",
    )
    snapshot = load_configuration(config_path=path)

    graph = load_graph_configuration(snapshot)
    provider = load_llm_provider_configuration(snapshot)
    chat = load_chat_configuration(provider)

    assert graph is not None
    assert graph.max_depth == 4
    assert graph.max_paths == 8
    assert provider is not None
    assert provider.api_base == "https://provider.example/v1?api_key=%2A%2A%2A"
    assert provider.credentials_configured is True
    assert "secret" not in repr(provider)
    assert chat is not None
    assert chat.model_name == "chat-model"


def test_runtime_sections_do_not_treat_package_defaults_as_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"
""",
        encoding="utf-8",
    )
    snapshot = load_configuration(config_path=path)

    assert load_graph_configuration(snapshot) is None
    assert load_llm_provider_configuration(snapshot) is None
    assert load_chat_configuration(None) is None
