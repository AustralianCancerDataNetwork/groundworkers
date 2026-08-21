"""Reading omop-emb's model registry for the setup console.

The registry is the list of models the embedding store actually holds vectors
for. It is distinct from the models a provider endpoint can serve: a provider
lists what it *could* embed with, the registry lists what has *been* embedded.
Grounding needs the latter, so model setup records both values.
"""

from __future__ import annotations

from collections.abc import Callable

from oa_configurator import Resolver  # type: ignore[import-untyped]

from groundworkers.application.setup.databases import classify_connection_error
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    EmbeddingStoreSnapshot,
    EmbeddingStoreState,
    RegisteredEmbeddingModel,
)
from groundworkers.config import GroundworkersConfig

__all__ = ["RegistryLister", "list_registered_models"]

RegistryLister = Callable[[ConfigurationSnapshot], EmbeddingStoreSnapshot]


def list_registered_models(snapshot: ConfigurationSnapshot) -> EmbeddingStoreSnapshot:
    """Probe the configured embedding store for its registered models.

    Returns an ``UNCONFIGURED`` snapshot rather than raising when no vector
    store is configured yet, since that is the ordinary state before the store
setup has been run.
    """
    if not snapshot.usable or snapshot.stack is None:
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNCONFIGURED, backend=None, reachable=False
        )
    store = None
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

    try:
        from omop_emb.backends import inspect_resolved_vector_store

        store = inspect_resolved_vector_store(resolved)
        records = store.registered_models()
        models_list = []
        for record in records:
            embeddings = store.stored_embeddings(record.model_name)
            models_list.append(
                RegisteredEmbeddingModel(
                    model_name=record.model_name,
                    provider=record.provider_type,
                    dimensions=int(record.dimensions),
                    metric=(record.metric_type.value if record.metric_type is not None else None),
                    index_type=record.index_type.value,
                    has_embeddings=bool(embeddings),
                    concept_count=len(embeddings),
                )
            )
        models = tuple(models_list)
    except Exception as exc:
        return EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.UNREACHABLE,
            backend=resolved.backend_type,
            reachable=False,
            failure=classify_connection_error(exc),
        )
    finally:
        if store is not None:
            store.close()
    return EmbeddingStoreSnapshot(
        state=(EmbeddingStoreState.POPULATED if any(item.has_embeddings for item in models) else EmbeddingStoreState.EMPTY),
        backend=resolved.backend_type,
        reachable=True,
        models=models,
    )
