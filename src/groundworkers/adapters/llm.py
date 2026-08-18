from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from groundworkers.base.errors import GroundworkersError

if TYPE_CHECKING:
    from omop_llm import ModelBackend

_STATUS_TIMEOUT_SECONDS = 2.0
_COMPLETION_TIMEOUT_SECONDS = 180.0


class LLMAdapter:
    """Groundworkers' error and payload boundary over one omop-llm ``ModelBackend``.

    The backend is provider-neutral (any-llm), so this adapter holds no
    provider-specific behaviour and no HTTP client of its own. What it does own
    is the translation into Groundworkers' contracts: ``GroundworkersError``
    codes instead of provider exceptions, and plain JSON-safe dicts for the MCP
    tool layer.

    Two completion modes are available:

    - **Text completion** (``complete_text``): returns a raw text response.
    - **Structured completion** (``complete_structured``): requests a JSON
      response matching a caller-supplied schema. Preferred for MCP-facing
      tools where downstream agents need to parse the output reliably.
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[[], ModelBackend],
    ) -> None:
        self._backend_factory = backend_factory
        self._backend: ModelBackend | None = None

    def is_available(self) -> bool:
        """Return True if the model backend is reachable."""
        return self.status()["available"]

    def close(self) -> None:
        """Release the cached backend."""
        self._backend = None

    def status(self) -> dict[str, Any]:
        """Return availability and configuration details. Never raises.

        Probes the provider with a short timeout. On failure returns
        ``{"available": False, ..., "detail": "<reason>"}``.
        """
        try:
            backend = self._get_backend()
            available = backend.is_available(timeout=_STATUS_TIMEOUT_SECONDS)
            return {
                "available": available,
                "provider": backend.provider,
                "default_model": backend.model,
                # Reported from the resolved model's declared capability rather
                # than assumed: omop-llm treats structured output as opt-in.
                "structured_output_supported": backend.capabilities.structured_output,
                "detail": None if available else "Provider did not respond to a model listing.",
            }
        except Exception as exc:
            # Broad except: status must never raise, it reports a category.
            return {
                "available": False,
                "provider": None,
                "default_model": None,
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

        ``model_name`` is accepted for call-site compatibility and must match the
        configured model: one resolved ``[models.*]`` entry backs this adapter,
        so an unrelated name cannot be honoured.

        Raises ``INVALID_INPUT`` if ``model_name`` names a different model.
        Raises ``BACKEND_UNAVAIL`` if the call fails.
        """
        backend = self._get_backend()
        self._check_model(backend, model_name)
        try:
            response = backend.complete(
                _build_messages(prompt, system_prompt),
                temperature=temperature,
                timeout=_COMPLETION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise GroundworkersError("BACKEND_UNAVAIL", f"LLM call failed: {exc}") from exc
        return {
            "text": response.choices[0].message.content,
            "model": response.model,
            "provider": backend.provider,
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
        from the provider. This is compatible with Ollama, vLLM, and OpenAI
        endpoints, and deliberately does not use omop-llm's ``extract()``: that
        requires a Pydantic model and a provider that declares native structured
        output, whereas callers here supply a raw JSON schema.

        The response is parsed but not validated against the schema — callers are
        responsible for validating the returned dict (e.g. with Pydantic).

        Raises ``INVALID_INPUT`` if ``model_name`` names a different model or if
        response_schema is not JSON-serializable.
        Raises ``BACKEND_UNAVAIL`` if the call fails.
        Raises ``QUERY_ERROR`` if the response is not valid JSON.
        """
        backend = self._get_backend()
        self._check_model(backend, model_name)
        try:
            schema_json = json.dumps(response_schema, indent=2)
        except (TypeError, ValueError) as exc:
            raise GroundworkersError(
                "INVALID_INPUT", f"response_schema is not JSON-serializable: {exc}"
            ) from exc
        schema_directive = f"Respond with a JSON object matching this schema:\n{schema_json}"
        augmented_system = f"{system_prompt}\n\n{schema_directive}" if system_prompt else schema_directive
        try:
            response = backend.complete(
                _build_messages(prompt, augmented_system),
                response_format={"type": "json_object"},
                temperature=temperature,
                timeout=_COMPLETION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise GroundworkersError("BACKEND_UNAVAIL", f"LLM call failed: {exc}") from exc
        content = response.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise GroundworkersError("QUERY_ERROR", f"LLM response was not valid JSON: {exc}") from exc

    def _get_backend(self) -> ModelBackend:
        if self._backend is None:
            try:
                self._backend = self._backend_factory()
            except Exception as exc:
                raise GroundworkersError(
                    "BACKEND_UNAVAIL", f"LLM backend could not be initialised: {exc}"
                ) from exc
        return self._backend

    @staticmethod
    def _check_model(backend: ModelBackend, model_name: str | None) -> None:
        if model_name is not None and model_name != backend.model:
            raise GroundworkersError(
                "INVALID_INPUT",
                f"Requested model {model_name!r} is not the configured chat model "
                f"{backend.model!r}. Configure it as a [models.*] entry and point "
                "groundworkers.llm_model_name at it.",
            )


def _build_messages(prompt: str, system_prompt: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages
