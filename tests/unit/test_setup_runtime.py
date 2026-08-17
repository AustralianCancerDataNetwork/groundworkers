from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.models import LlmModelMetadata
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
    verify_llm_provider,
)


def test_runtime_sections_load_from_plain_tool_mappings_and_redact_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.omop_graph]
max_depth = 4
max_paths = 8

[tools.groundworkers]
cdm_db = "cdm_db"

[tools.groundworkers.llm]
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
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"
""",
        encoding="utf-8",
    )
    snapshot = load_configuration(config_path=path)

    assert load_graph_configuration(snapshot) is None
    assert load_llm_provider_configuration(snapshot) is None
    assert load_chat_configuration(None) is None


def test_llm_provider_check_reports_available_configured_model(tmp_path: Path) -> None:
    path = _write_llm_config(tmp_path, model="chat-model")
    snapshot = load_configuration(config_path=path)

    result = verify_llm_provider(
        snapshot,
        client_factory=lambda _llm: _FakeLlmClient(("chat-model", "other-model")),
    )

    assert result is not None
    assert result.reachable is True
    assert result.model_available is True
    assert result.ready is True
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "llm_endpoint_reachable",
        "llm_model_available",
    }


def test_llm_provider_check_reports_missing_configured_model(tmp_path: Path) -> None:
    path = _write_llm_config(tmp_path, model="missing-model")
    snapshot = load_configuration(config_path=path)

    result = verify_llm_provider(
        snapshot,
        client_factory=lambda _llm: _FakeLlmClient(("chat-model",)),
    )

    assert result is not None
    assert result.reachable is True
    assert result.model_available is False
    assert result.ready is False
    assert result.has_errors is True


def test_llm_provider_check_enriches_ollama_model_metadata(tmp_path: Path) -> None:
    path = _write_llm_config(
        tmp_path,
        model="chat-model",
        provider="ollama",
        api_base="http://localhost:11434/v1",
    )
    snapshot = load_configuration(config_path=path)

    result = verify_llm_provider(
        snapshot,
        client_factory=lambda _llm: _FakeLlmClient(("chat-model",)),
        metadata_factory=lambda _llm: (
            LlmModelMetadata(
                name="chat-model",
                size_bytes=4_294_967_296,
                parameter_size="7B",
                quantization_level="Q4_K_M",
                family="llama",
            ),
        ),
    )

    assert result is not None
    assert result.ready is True
    assert result.model_metadata[0].name == "chat-model"
    assert result.model_metadata[0].parameter_size == "7B"
    assert "llm_ollama_metadata_available" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_llm_provider_check_merges_ollama_metadata_into_inventory(
    tmp_path: Path,
) -> None:
    path = _write_llm_config(
        tmp_path,
        model="chat-model",
        provider="ollama",
        api_base="http://localhost:11434/v1",
    )
    snapshot = load_configuration(config_path=path)

    result = verify_llm_provider(
        snapshot,
        client_factory=lambda _llm: _FakeLlmClient(("chat-model",)),
        metadata_factory=lambda _llm: (
            LlmModelMetadata(name="chat-model"),
            LlmModelMetadata(name="other-model"),
        ),
    )

    assert result is not None
    assert result.inventory == ("chat-model", "other-model")
    assert result.model_available is True


class _FakeModels:
    def __init__(self, model_ids: tuple[str, ...]) -> None:
        self._model_ids = model_ids

    def list(self, *, timeout: float):
        assert timeout > 0
        return SimpleNamespace(
            data=tuple(SimpleNamespace(id=model_id) for model_id in self._model_ids)
        )


class _FakeLlmClient:
    def __init__(self, model_ids: tuple[str, ...]) -> None:
        self.models = _FakeModels(model_ids)


def _write_llm_config(
    tmp_path: Path,
    *,
    model: str,
    provider: str = "openai-compatible",
    api_base: str = "https://provider.example/v1?api_key=secret",
) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f"""
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"

[tools.groundworkers.llm]
enabled = true
provider = "{provider}"
api_base = "{api_base}"
api_key = "also-secret"
default_model_name = "{model}"
""",
        encoding="utf-8",
    )
    return path
