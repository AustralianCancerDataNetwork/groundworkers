from pathlib import Path
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.base.errors import GroundworkersError
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(content: str, model: str = "test-model") -> object:
    """Build a minimal chat completion response object."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], model=model)


def _make_models_list() -> object:
    return SimpleNamespace(data=[SimpleNamespace(id="test-model")])


def _adapter(*, client: object, provider: str = "openai-compatible",
             default_model: str | None = "test-model") -> LLMAdapter:
    return LLMAdapter(
        provider=provider,
        default_model_name=default_model,
        client_factory=lambda: client,
    )


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

def test_status_available_when_models_list_succeeds():
    mock_client = MagicMock()
    mock_client.models.list.return_value = _make_models_list()
    adapter = _adapter(client=mock_client)

    result = adapter.status()

    assert result["available"] is True
    assert result["provider"] == "openai-compatible"
    assert result["default_model"] == "test-model"
    assert result["structured_output_supported"] is True
    assert result["detail"] is None


def test_status_unavailable_when_models_list_raises():
    mock_client = MagicMock()
    mock_client.models.list.side_effect = ConnectionError("refused")
    adapter = _adapter(client=mock_client)

    result = adapter.status()

    assert result["available"] is False
    assert result["detail"] is not None
    assert "refused" in result["detail"]


def test_status_never_raises_when_client_factory_fails():
    adapter = LLMAdapter(
        provider="openai-compatible",
        client_factory=lambda: (_ for _ in ()).throw(RuntimeError("factory failed")),
    )
    result = adapter.status()
    assert result["available"] is False
    assert result["detail"] is not None


# ---------------------------------------------------------------------------
# complete_text()
# ---------------------------------------------------------------------------

def test_complete_text_returns_response():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response("Hello world")
    adapter = _adapter(client=mock_client)

    result = adapter.complete_text("Say hello")

    assert result["text"] == "Hello world"
    assert result["model"] == "test-model"
    assert result["provider"] == "openai-compatible"


def test_complete_text_uses_system_prompt():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response("ok")
    adapter = _adapter(client=mock_client)

    adapter.complete_text("Do X", system_prompt="You are a helper")

    call_kwargs = mock_client.chat.completions.create.call_args[1]
    messages = call_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helper"
    assert messages[1]["role"] == "user"


def test_complete_text_overrides_model_name():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response("ok", model="other-model")
    adapter = _adapter(client=mock_client, default_model="default-model")

    adapter.complete_text("Prompt", model_name="other-model")

    call_kwargs = mock_client.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "other-model"


def test_complete_text_raises_backend_unavail_on_api_error():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("timeout")
    adapter = _adapter(client=mock_client)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_text("Prompt")

    assert exc_info.value.code == "BACKEND_UNAVAIL"


def test_complete_text_raises_invalid_input_with_no_model():
    mock_client = MagicMock()
    adapter = _adapter(client=mock_client, default_model=None)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_text("Prompt")

    assert exc_info.value.code == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# complete_structured()
# ---------------------------------------------------------------------------

def test_complete_structured_parses_json_response():
    schema = {"type": "object", "properties": {"label": {"type": "string"}}}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response('{"label": "diabetes"}')
    adapter = _adapter(client=mock_client)

    result = adapter.complete_structured("Classify this", schema)

    assert result == {"label": "diabetes"}


def test_complete_structured_injects_schema_into_system_prompt():
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response('{"value": 1}')
    adapter = _adapter(client=mock_client)

    adapter.complete_structured("Prompt", schema, system_prompt="Be concise")

    call_kwargs = mock_client.chat.completions.create.call_args[1]
    system_msg = call_kwargs["messages"][0]["content"]
    assert "Be concise" in system_msg
    assert "json" in system_msg.lower()
    assert "value" in system_msg


def test_complete_structured_requests_json_object_format():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response('{"x": 1}')
    adapter = _adapter(client=mock_client)

    adapter.complete_structured("Prompt", {})

    call_kwargs = mock_client.chat.completions.create.call_args[1]
    assert call_kwargs.get("response_format") == {"type": "json_object"}


def test_complete_structured_raises_query_error_on_invalid_json():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response("not json at all")
    adapter = _adapter(client=mock_client)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_structured("Prompt", {})

    assert exc_info.value.code == "QUERY_ERROR"


def test_complete_structured_raises_backend_unavail_on_api_error():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("connection refused")
    adapter = _adapter(client=mock_client)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_structured("Prompt", {})

    assert exc_info.value.code == "BACKEND_UNAVAIL"


# ---------------------------------------------------------------------------
# close() and lifecycle
# ---------------------------------------------------------------------------

def test_close_drops_cached_client():
    call_count = 0

    def factory():
        nonlocal call_count
        call_count += 1
        mock = MagicMock()
        mock.models.list.return_value = _make_models_list()
        return mock

    adapter = LLMAdapter(provider="test", client_factory=factory)
    adapter.status()
    assert call_count == 1
    adapter.close()
    adapter.status()
    assert call_count == 2
