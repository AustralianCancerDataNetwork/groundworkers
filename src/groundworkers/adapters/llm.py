from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from groundworkers.base.errors import GroundworkersError

_STATUS_TIMEOUT_SECONDS = 2.0
_COMPLETION_TIMEOUT_SECONDS = 180.0


class LLMAdapter:
    """Adapter for OpenAI-compatible LLM chat completion APIs.

    Works with any provider that implements the OpenAI chat completions API:
    local deployments (Ollama, vLLM, LM Studio) and remote services (OpenAI,
    Azure OpenAI, and compatible cloud APIs). Configure ``api_base`` to point
    at the correct endpoint.

    Two completion modes are available:

    - **Text completion** (``complete_text``): returns a raw text response.
    - **Structured completion** (``complete_structured``): requests a JSON
      response matching a caller-supplied schema. Preferred for MCP-facing
      tools where downstream agents need to parse the output reliably.
    """

    def __init__(
        self,
        *,
        provider: str,
        default_model_name: str | None = None,
        client_factory: Callable[[], Any],
    ) -> None:
        self._provider = provider
        self._default_model_name = default_model_name
        self._client_factory = client_factory
        self._client: Any = None

    def is_available(self) -> bool:
        """Return True if the LLM API is reachable."""
        return self.status()["available"]

    def close(self) -> None:
        """Release the cached client."""
        self._client = None

    def status(self) -> dict[str, Any]:
        """Return availability and configuration details. Never raises.

        Probes the API with a short timeout. On failure returns
        ``{"available": False, ..., "detail": "<reason>"}``.
        """
        try:
            client = self._get_client()
            client.models.list(timeout=_STATUS_TIMEOUT_SECONDS)
            return {
                "available": True,
                "provider": self._provider,
                "default_model": self._default_model_name,
                "structured_output_supported": True,
                "detail": None,
            }
        except Exception as exc:
            return {
                "available": False,
                "provider": self._provider,
                "default_model": self._default_model_name,
                "structured_output_supported": None,
                "detail": repr(exc),
            }

    def complete_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        model_name: str | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Complete a prompt and return the response text.

        Raises ``INVALID_INPUT`` if no model is resolvable.
        Raises ``BACKEND_UNAVAIL`` if the API call fails.
        """
        client = self._get_client()
        resolved_model = self._resolve_model(model_name)
        messages = _build_messages(prompt, system_prompt)
        try:
            response = client.chat.completions.create(
                model=resolved_model,
                messages=messages,
                temperature=temperature,
                timeout=_COMPLETION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise GroundworkersError("BACKEND_UNAVAIL", f"LLM call failed: {exc}") from exc
        return {
            "text": response.choices[0].message.content,
            "model": response.model,
            "provider": self._provider,
        }

    def complete_structured(
        self,
        prompt: str,
        response_schema: dict[str, Any],
        *,
        system_prompt: str | None = None,
        model_name: str | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Complete a prompt and return a parsed JSON dict guided by response_schema.

        The schema is injected into the system prompt and JSON mode is requested
        from the API. This is compatible with Ollama, vLLM, and OpenAI endpoints.

        The response is parsed but not validated against the schema — callers are
        responsible for validating the returned dict (e.g. with Pydantic).

        Raises ``INVALID_INPUT`` if no model is resolvable or if response_schema
        is not JSON-serializable.
        Raises ``BACKEND_UNAVAIL`` if the API call fails.
        Raises ``QUERY_ERROR`` if the response is not valid JSON.
        """
        client = self._get_client()
        resolved_model = self._resolve_model(model_name)
        try:
            schema_json = json.dumps(response_schema, indent=2)
        except (TypeError, ValueError) as exc:
            raise GroundworkersError(
                "INVALID_INPUT", f"response_schema is not JSON-serializable: {exc}"
            ) from exc
        schema_directive = f"Respond with a JSON object matching this schema:\n{schema_json}"
        augmented_system = f"{system_prompt}\n\n{schema_directive}" if system_prompt else schema_directive
        messages = _build_messages(prompt, augmented_system)
        try:
            response = client.chat.completions.create(
                model=resolved_model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
                timeout=_COMPLETION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise GroundworkersError("BACKEND_UNAVAIL", f"LLM call failed: {exc}") from exc
        content = response.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise GroundworkersError("QUERY_ERROR", f"LLM response was not valid JSON: {exc}") from exc

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                self._client = self._client_factory()
            except Exception as exc:
                raise GroundworkersError("BACKEND_UNAVAIL", f"LLM client could not be initialised: {exc}") from exc
        return self._client

    def _resolve_model(self, model_name: str | None) -> str:
        resolved = model_name or self._default_model_name
        if resolved is None:
            raise GroundworkersError(
                "INVALID_INPUT",
                "No model specified and no default model is configured",
            )
        return resolved


def _build_messages(prompt: str, system_prompt: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages
