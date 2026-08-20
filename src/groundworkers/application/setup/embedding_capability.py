"""One truthful capability verdict for every embedding setup surface."""

from __future__ import annotations

from dataclasses import dataclass

from groundworkers.application.setup.models import (
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
    ModelReconciliation,
)


@dataclass(frozen=True)
class EmbeddingCapabilityState:
    configured: bool
    store_initialized: bool
    provider_model_verified: bool
    model_registered: bool
    coverage_available: bool
    coverage_complete: bool
    index_ready: bool
    ready: bool
    can_populate: bool
    blockers: tuple[str, ...]


def embedding_capability_state(
    configuration: EmbeddingConfiguration | None,
    coverage: EmbeddingCoverageReport | None,
    reconciliation: ModelReconciliation | None,
) -> EmbeddingCapabilityState:
    """Resolve setup, verification, coverage, and index evidence once.

    Coverage alone is deliberately insufficient: reading an uninitialised store
    can still produce counts, and a configured provider has not necessarily
    encoded successfully. Population may register a new compatible model, while
    the stronger Ready verdict requires that model to already be registered.
    """

    configured = configuration is not None
    coverage_available = coverage is not None and coverage.coverage.available
    store_initialized = bool(
        coverage_available
        and coverage is not None
        and coverage.coverage.metadata.get("store_initialized") is True
    )
    provider = reconciliation.provider if reconciliation is not None else None
    provider_model_verified = bool(
        reconciliation is not None
        and reconciliation.ready_for_population
        and provider is not None
        and provider.reachable
        and provider.encoding_succeeded
        and reconciliation.store is not None
        and reconciliation.store.reachable
    )
    model_registered = bool(
        reconciliation is not None and reconciliation.model_is_registered
    )
    coverage_complete = bool(
        coverage_available
        and coverage is not None
        and coverage.coverage.pending_total == 0
    )
    index_ready = bool(coverage_available and coverage is not None and _index_ready(coverage))
    can_populate = bool(
        configured
        and store_initialized
        and provider_model_verified
        and coverage_available
        and coverage is not None
        and coverage.coverage.pending_total > 0
    )
    ready = bool(
        configured
        and store_initialized
        and provider_model_verified
        and model_registered
        and coverage_complete
        and index_ready
    )

    blockers: list[str] = []
    if not configured:
        blockers.append("Configure an embedding model and vector store.")
    if configured and not store_initialized:
        blockers.append("Initialize the embedding store explicitly.")
    if configured and not provider_model_verified:
        blockers.append("Check that the provider, model, and store are compatible.")
    if configured and not coverage_available:
        blockers.append("Refresh embedding coverage successfully.")
    if coverage_available and not coverage_complete:
        blockers.append("Populate the pending concept embeddings.")
    if coverage_available and coverage_complete and not model_registered:
        blockers.append("Register the configured model in the embedding store.")
    if coverage_available and not index_ready:
        blockers.append("Build the configured physical embedding index.")

    return EmbeddingCapabilityState(
        configured=configured,
        store_initialized=store_initialized,
        provider_model_verified=provider_model_verified,
        model_registered=model_registered,
        coverage_available=coverage_available,
        coverage_complete=coverage_complete,
        index_ready=index_ready,
        ready=ready,
        can_populate=can_populate,
        blockers=tuple(blockers),
    )


def _index_ready(report: EmbeddingCoverageReport) -> bool:
    index = report.index
    if not index.registered:
        return False
    if index.registry_index_type == "flat":
        return True
    if index.registry_index_type is None:
        return False
    return index.has_physical_index


__all__ = ["EmbeddingCapabilityState", "embedding_capability_state"]
