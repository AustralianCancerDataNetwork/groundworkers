"""Reading omop-emb's model registry for the setup console.

The registry is the list of models the embedding store actually holds vectors
for. It is distinct from the models a provider endpoint can serve: a provider
lists what it *could* embed with, the registry lists what has *been* embedded.
Grounding needs the latter, which is why the model journey offers both.
"""

from __future__ import annotations

from collections.abc import Callable

from oa_configurator import Resolver  # type: ignore[import-untyped]

from groundworkers.application.setup.embedding_setup import probe_embedding_store
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    EmbeddingStoreSnapshot,
    EmbeddingStoreState,
)
from groundworkers.config import GroundworkersConfig

__all__ = ["RegistryLister", "list_registered_models"]

RegistryLister = Callable[[ConfigurationSnapshot], EmbeddingStoreSnapshot]


def list_registered_models(snapshot: ConfigurationSnapshot) -> EmbeddingStoreSnapshot:
    """Probe the configured embedding store for its registered models.

    Returns an ``UNCONFIGURED`` snapshot rather than raising when no vector
    store is configured yet, since that is the ordinary state before the store
    journey has been run.
    """
    if not snapshot.usable or snapshot.stack is None:
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNCONFIGURED, backend=None, reachable=False
        )
    try:
        groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
        if groundworkers.vector_store_name is None:
            return EmbeddingStoreSnapshot(
                state=EmbeddingStoreState.UNCONFIGURED, backend=None, reachable=False
            )
        resolved = Resolver(snapshot.stack).resolve_vector_store(
            groundworkers.vector_store_name
        )
    except Exception:
        # Broad except: an unresolvable store is reported as unconfigured, not raised.
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNCONFIGURED, backend=None, reachable=False
        )

    def backend_factory():
        from omop_emb.backends import resolve_backend_from_resolved_vector_store

        return resolve_backend_from_resolved_vector_store(resolved)

    return probe_embedding_store(
        backend_factory, backend_type=resolved.backend_type
    )
