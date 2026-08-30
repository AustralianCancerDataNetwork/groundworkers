from __future__ import annotations

import json
from pathlib import Path

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.models import LlmModelMetadata
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
    verify_llm_provider,
)


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


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

[tools.groundworkers]
cdm_db = "cdm_db"
llm_model_name = "chat_model"

grounding_min_fulltext_overlap = 0.25
grounding_max_depth = 4

[providers.chat_provider]
provider = "openai"
base_url = "https://provider.example/v1?api_key=secret"
api_key = "also-secret"

[models.chat_model]
provider = "chat_provider"
model = "chat-model"
structured_output = true
""",
        encoding="utf-8",
    )
    snapshot = load_configuration(config_path=path)

    graph = load_graph_configuration(snapshot)
    provider = load_llm_provider_configuration(snapshot)
    chat = load_chat_configuration(provider)

    assert graph is not None
    # Graph/grounding policy comes from Groundworkers' own configuration, not from
    # omop-graph's internal package config.
    assert graph.cdm_database_name == "cdm_db"
    assert graph.vocabulary_schema == "main"
    assert graph.grounding_max_depth == 4
    assert graph.min_fulltext_overlap == 0.25
    assert provider is not None
    assert provider.api_base == "https://provider.example/v1?api_key=***"
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
        inventory_factory=lambda _llm: ("chat-model", "other-model"),
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
        inventory_factory=lambda _llm: ("chat-model",),
    )

    assert result is not None
    assert result.reachable is True
    assert result.model_available is False
    assert result.ready is False
    assert result.has_errors is True


def test_ollama_inventory_uses_native_endpoint_with_or_without_v1(
    monkeypatch, tmp_path: Path
) -> None:
    path = _write_llm_config(
        tmp_path,
        model="snowflake-arctic-embed2:local-2026-06-30",
        provider="ollama",
        api_base="http://localhost:11434/v1",
    )
    snapshot = load_configuration(config_path=path)
    requested: list[str] = []

    def fake_urlopen(request, timeout):
        del timeout
        requested.append(request if isinstance(request, str) else request.full_url)
        return _JsonResponse(
            {
                "models": [
                    {
                        "name": "snowflake-arctic-embed2:local-2026-06-30",
                        "details": {"embedding_length": 1024},
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "groundworkers.application.setup.runtime_setup.urlopen", fake_urlopen
    )
    result = verify_llm_provider(snapshot)

    assert result is not None
    assert result.reachable is True
    assert result.model_available is True
    assert requested == ["http://localhost:11434/api/tags"]


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
        inventory_factory=lambda _llm: ("chat-model",),
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
        inventory_factory=lambda _llm: ("chat-model",),
        metadata_factory=lambda _llm: (
            LlmModelMetadata(name="chat-model"),
            LlmModelMetadata(name="other-model"),
        ),
    )

    assert result is not None
    assert result.inventory == ("chat-model", "other-model")
    assert result.model_available is True


def test_vllm_inventory_adds_missing_v1_suffix(monkeypatch, tmp_path: Path) -> None:
    path = _write_llm_config(
        tmp_path,
        model="qwen3-coder",
        provider="vllm",
        api_base="http://localhost:8000",
    )
    snapshot = load_configuration(config_path=path)
    requested: list[str] = []

    def fake_urlopen(request, timeout):
        del timeout
        requested.append(request.full_url)
        return _JsonResponse({"data": [{"id": "qwen3-coder"}]})

    monkeypatch.setattr(
        "groundworkers.application.setup.runtime_setup.urlopen", fake_urlopen
    )
    result = verify_llm_provider(snapshot)

    assert result is not None
    assert result.reachable is True
    assert result.model_available is True
    assert requested == ["http://localhost:8000/v1/models"]


def test_vllm_inventory_leaves_existing_v1_suffix_alone(
    monkeypatch, tmp_path: Path
) -> None:
    path = _write_llm_config(
        tmp_path,
        model="qwen3-coder",
        provider="vllm",
        api_base="http://localhost:8000/v1",
    )
    snapshot = load_configuration(config_path=path)
    requested: list[str] = []

    def fake_urlopen(request, timeout):
        del timeout
        requested.append(request.full_url)
        return _JsonResponse({"data": [{"id": "qwen3-coder"}]})

    monkeypatch.setattr(
        "groundworkers.application.setup.runtime_setup.urlopen", fake_urlopen
    )
    result = verify_llm_provider(snapshot)

    assert result is not None
    assert result.reachable is True
    # Not doubled to ".../v1/v1/models".
    assert requested == ["http://localhost:8000/v1/models"]


def test_anthropic_inventory_uses_native_auth_and_version_header(
    monkeypatch, tmp_path: Path
) -> None:
    path = _write_llm_config(
        tmp_path,
        model="claude-model",
        provider="anthropic",
        api_base="https://api.anthropic.com",
    )
    snapshot = load_configuration(config_path=path)
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        del timeout
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _JsonResponse({"data": [{"id": "claude-model"}]})

    monkeypatch.setattr(
        "groundworkers.application.setup.runtime_setup.urlopen", fake_urlopen
    )
    result = verify_llm_provider(snapshot)

    assert result is not None
    assert result.reachable is True
    assert result.model_available is True
    assert seen["url"] == "https://api.anthropic.com/v1/models"
    # Anthropic authenticates with x-api-key + a pinned version header, not
    # OpenAI's Authorization: Bearer -- sending the wrong one looks like an
    # unreachable endpoint rather than an auth failure.
    assert seen["headers"]["x-api-key"] == "also-secret"
    assert "anthropic-version" in seen["headers"]
    assert "authorization" not in seen["headers"]


def test_gemini_inventory_uses_key_query_param_and_strips_name_prefix(
    monkeypatch, tmp_path: Path
) -> None:
    path = _write_llm_config(
        tmp_path,
        model="gemini-model",
        provider="gemini",
        api_base="https://generativelanguage.googleapis.com/v1beta",
    )
    snapshot = load_configuration(config_path=path)
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        del timeout
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _JsonResponse({"models": [{"name": "models/gemini-model"}]})

    monkeypatch.setattr(
        "groundworkers.application.setup.runtime_setup.urlopen", fake_urlopen
    )
    result = verify_llm_provider(snapshot)

    assert result is not None
    assert result.reachable is True
    assert result.model_available is True
    assert seen["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models?key=also-secret"
    )
    # Gemini takes the key as a query parameter, not a header.
    assert "authorization" not in seen["headers"]
    assert "x-api-key" not in seen["headers"]


def _write_llm_config(
    tmp_path: Path,
    *,
    model: str,
    provider: str = "openai",
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
llm_model_name = "chat_model"

[providers.chat_provider]
provider = "{provider}"
base_url = "{api_base}"
api_key = "also-secret"

[models.chat_model]
provider = "chat_provider"
model = "{model}"
structured_output = true
""",
        encoding="utf-8",
    )
    return path
