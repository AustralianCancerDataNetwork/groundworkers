from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.base.errors import GroundworkersError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(content: str, model: str = "test-model") -> object:
    """Build a minimal chat completion response object."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], model=model)


def _make_backend(
    *,
    provider: str = "ollama",
    model: str = "test-model",
    structured_output: bool = True,
) -> MagicMock:
    """A stand-in for one resolved omop-llm ModelBackend."""
    backend = MagicMock()
    backend.provider = provider
    backend.model = model
    backend.capabilities = SimpleNamespace(structured_output=structured_output)
    backend.is_available.return_value = True
    return backend


def _adapter(*, backend: object) -> LLMAdapter:
    return LLMAdapter(backend_factory=lambda: backend)


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

def test_status_available_when_backend_is_reachable():
    backend = _make_backend()
    adapter = _adapter(backend=backend)

    result = adapter.status()

    assert result["available"] is True
    assert result["provider"] == "ollama"
    assert result["default_model"] == "test-model"
    assert result["structured_output_supported"] is True
    assert result["detail"] is None
    backend.is_available.assert_called_once_with()


def test_status_reports_the_models_declared_structured_output_capability():
    """Capability is read off the resolved entry, not assumed."""
    adapter = _adapter(backend=_make_backend(structured_output=False))

    assert adapter.status()["structured_output_supported"] is False


def test_status_unavailable_when_backend_cannot_be_reached():
    backend = _make_backend()
    backend.is_available.return_value = False
    adapter = _adapter(backend=backend)

    result = adapter.status()

    assert result["available"] is False
    assert result["detail"] is not None


def test_status_never_raises_when_backend_factory_fails():
    adapter = LLMAdapter(
        backend_factory=lambda: (_ for _ in ()).throw(RuntimeError("factory failed")),
    )
    result = adapter.status()
    assert result["available"] is False
    assert result["detail"] is not None


# ---------------------------------------------------------------------------
# complete_text()
# ---------------------------------------------------------------------------

def test_complete_text_returns_response():
    backend = _make_backend()
    backend.complete.return_value = _make_response("Hello world")
    adapter = _adapter(backend=backend)

    result = adapter.complete_text("Say hello")

    assert result["text"] == "Hello world"
    assert result["model"] == "test-model"
    assert result["provider"] == "ollama"
    assert backend.complete.call_args.kwargs["timeout"] == 180.0


def test_complete_text_uses_system_prompt():
    backend = _make_backend()
    backend.complete.return_value = _make_response("ok")
    adapter = _adapter(backend=backend)

    adapter.complete_text("Do X", system_prompt="You are a helper")

    messages = backend.complete.call_args[0][0]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helper"
    assert messages[1]["role"] == "user"


def test_complete_text_accepts_the_configured_model_name():
    backend = _make_backend(model="configured-model")
    backend.complete.return_value = _make_response("ok", model="configured-model")
    adapter = _adapter(backend=backend)

    adapter.complete_text("Prompt", model_name="configured-model")

    assert backend.complete.called


def test_complete_text_rejects_a_model_name_that_is_not_configured():
    """One resolved [models.*] entry backs the adapter; it cannot switch model."""
    backend = _make_backend(model="configured-model")
    adapter = _adapter(backend=backend)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_text("Prompt", model_name="some-other-model")

    assert exc_info.value.code == "INVALID_INPUT"
    assert not backend.complete.called


def test_complete_text_raises_backend_unavail_on_api_error():
    backend = _make_backend()
    backend.complete.side_effect = RuntimeError("timeout")
    adapter = _adapter(backend=backend)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_text("Prompt")

    assert exc_info.value.code == "BACKEND_UNAVAIL"


# ---------------------------------------------------------------------------
# complete_structured()
# ---------------------------------------------------------------------------

def test_complete_structured_parses_json_response():
    schema = {"type": "object", "properties": {"label": {"type": "string"}}}
    backend = _make_backend()
    backend.complete.return_value = _make_response('{"label": "diabetes"}')
    adapter = _adapter(backend=backend)

    result = adapter.complete_structured("Classify this", schema)

    assert result == {"label": "diabetes"}


def test_complete_structured_injects_schema_into_system_prompt():
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}}
    backend = _make_backend()
    backend.complete.return_value = _make_response('{"value": 1}')
    adapter = _adapter(backend=backend)

    adapter.complete_structured("Prompt", schema, system_prompt="Be concise")

    system_msg = backend.complete.call_args[0][0][0]["content"]
    assert "Be concise" in system_msg
    assert "json" in system_msg.lower()
    assert "value" in system_msg


def test_complete_structured_requests_json_object_format():
    backend = _make_backend()
    backend.complete.return_value = _make_response('{"x": 1}')
    adapter = _adapter(backend=backend)

    adapter.complete_structured("Prompt", {})

    call_kwargs = backend.complete.call_args.kwargs
    assert call_kwargs.get("response_format") == {"type": "json_object"}
    assert call_kwargs["timeout"] == 180.0


def test_complete_structured_raises_query_error_on_invalid_json():
    backend = _make_backend()
    backend.complete.return_value = _make_response("not json at all")
    adapter = _adapter(backend=backend)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_structured("Prompt", {})

    assert exc_info.value.code == "QUERY_ERROR"


def test_complete_structured_raises_backend_unavail_on_api_error():
    backend = _make_backend()
    backend.complete.side_effect = RuntimeError("connection refused")
    adapter = _adapter(backend=backend)

    with pytest.raises(GroundworkersError) as exc_info:
        adapter.complete_structured("Prompt", {})

    assert exc_info.value.code == "BACKEND_UNAVAIL"


# ---------------------------------------------------------------------------
# close() and lifecycle
# ---------------------------------------------------------------------------

def test_close_drops_cached_backend():
    call_count = 0

    def factory():
        nonlocal call_count
        call_count += 1
        return _make_backend()

    adapter = LLMAdapter(backend_factory=factory)
    adapter.status()
    assert call_count == 1
    adapter.close()
    adapter.status()
    assert call_count == 2
