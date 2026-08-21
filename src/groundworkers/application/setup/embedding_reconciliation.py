"""Answer whether the configured embedding model can actually be used.

Three separately-configured things have to agree before the embedding tier does
anything: the ``[models.*]`` entry, the vector store that must hold its vectors,
and the provider that must produce them. Each is checked elsewhere -- the store
by :func:`~groundworkers.application.setup.embedding_registry.list_registered_models`,
the provider by :func:`~groundworkers.application.setup.embedding_setup.probe_provider`,
the verdict by :func:`~groundworkers.application.setup.embedding_setup.reconcile_models`.

This module is the one place that runs all three together, so the setup console
can report readiness rather than reporting that two names resolve.

It lives apart from ``embedding_setup`` to keep the import direction one-way:
``embedding_registry`` already depends on ``embedding_setup``, and this needs
both.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from oa_configurator import ResolvedModel, Resolver  # type: ignore[import-untyped]

from groundworkers.application.setup.embedding_registry import (
    RegistryLister,
    list_registered_models,
)
from groundworkers.application.setup.embedding_setup import (
    probe_provider,
    reconcile_models,
)
from groundworkers.application.setup.model_inventory import discover_provider_models
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    ModelReconciliation,
    ProviderSnapshot,
)
from groundworkers.config import GroundworkersConfig

__all__ = ["verify_embedding_model"]

ProviderProber = Callable[..., ProviderSnapshot]
InventoryDiscoverer = Callable[[str, str | None, str | None], Sequence[str]]


def verify_embedding_model(
    snapshot: ConfigurationSnapshot,
    *,
    registry_lister: RegistryLister = list_registered_models,
    provider_prober: ProviderProber = probe_provider,
    inventory_discoverer: InventoryDiscoverer = discover_provider_models,
) -> ModelReconciliation | None:
    """Reconcile the configured embedding model with its store and provider.

    Returns ``None`` when the embedding tier is not configured at all, matching
    :func:`~groundworkers.application.setup.embedding_setup.load_embedding_configuration`
    so the console never shows a verdict for a tier it is also calling absent.
    Both references are required: a model with nowhere to put its vectors, or a
    store with nothing to fill it, is unconfigured rather than broken.
    """

    resolved_model = _resolved_embedding_model(snapshot)
    if resolved_model is None:
        return None

    store = registry_lister(snapshot)
    provider = provider_prober(
        resolved_model,
        inventory_discoverer=lambda model: inventory_discoverer(
            model.provider.provider,
            model.provider.base_url,
            model.provider.api_key,
        ),
    )
    return reconcile_models(
        configured_model=provider.configured_model,
        registered_models=store.models,
        provider=provider,
        store=store,
    )


def _resolved_embedding_model(
    snapshot: ConfigurationSnapshot,
) -> ResolvedModel | None:
    if not snapshot.usable or snapshot.stack is None:
        return None
    try:
        groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
        if (
            groundworkers.embedding_model_name is None
            or groundworkers.vector_store_name is None
        ):
            return None
        return Resolver(snapshot.stack).resolve_model(
            groundworkers.embedding_model_name
        )
    except Exception:
        # Broad except: an unresolvable reference is the configuration state the
        # console already reports; it is not a verdict about the model.
        return None
