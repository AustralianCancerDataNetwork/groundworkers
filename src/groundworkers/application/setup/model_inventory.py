"""Provider-neutral live model inventory for the setup write flows.

Both setup journeys that choose a model — the embedding model and the chat LLM —
need the list of models a provider endpoint actually serves, so the wizard can
offer real choices instead of a free-text field.

omop-llm can build a ``ModelBackend``, embed text, report dimensions, and answer
``is_available()``, but it does not expose the provider's inventory call. The
upstream request for a normalized public inventory API is filed; until it lands,
inventory is obtained here from the provider's own public HTTP surface (the
OpenAI-compatible ``/models`` endpoint, plus Ollama's native tag listing for
richer names). This module is the single injected seam for that.

It deliberately does **not** reach into ``ModelBackend._client`` or import
any-llm internals, and it holds no per-provider behaviour beyond what the shared
provider check already implements.
"""

from __future__ import annotations

from collections.abc import Sequence

from groundworkers.application.setup.models import DiagnosticSeverity
from groundworkers.application.setup.runtime_setup import verify_llm_config
from groundworkers.config import LLMConfig

__all__ = ["InventoryUnavailable", "discover_provider_models"]


class InventoryUnavailable(RuntimeError):
    """The provider endpoint could not be asked for its model inventory.

    Carries a redacted message only: provider errors routinely embed endpoints,
    credentials, and connection strings.
    """


def discover_provider_models(
    provider_kind: str,
    base_url: str | None,
    api_key: str | None,
) -> Sequence[str]:
    """Return the model identifiers a provider endpoint reports, in its order.

    Parameters
    ----------
    provider_kind:
        The provider key, e.g. ``"ollama"`` or ``"openai"``.
    base_url:
        The provider endpoint. ``None`` lets the client use its own default.
    api_key:
        Optional credential. Never included in raised messages.

    Raises
    ------
    InventoryUnavailable
        The endpoint was unreachable, rejected the request, or returned no
        models. The message is safe to show an operator.
    """
    result = verify_llm_config(
        LLMConfig(
            enabled=True,
            provider=provider_kind,
            api_base=base_url,
            api_key=api_key,
            default_model_name=None,
        ),
        require_default_model=False,
    )
    if not result.reachable:
        raise InventoryUnavailable(_failure_detail(result))
    inventory = tuple(result.inventory or ())
    if not inventory:
        raise InventoryUnavailable(
            "The provider endpoint responded but reported no available models."
        )
    return inventory


def _failure_detail(result) -> str:
    if result.failure is not None:
        return result.failure.detail
    for diagnostic in result.diagnostics:
        if diagnostic.severity is DiagnosticSeverity.ERROR:
            return diagnostic.message
    return "The provider endpoint could not be reached for its model inventory."
