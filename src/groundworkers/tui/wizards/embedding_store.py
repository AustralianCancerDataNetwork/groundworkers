from __future__ import annotations

from collections.abc import Mapping

from groundskeeping.contracts.wizards import (
    ReviewStep,
    WizardResult,
    WizardResultStatus,
    WizardReview,
    WizardSnapshot,
    WizardSpec,
    WizardTransition,
)

from groundworkers.application.setup.embedding_setup import initialize_embedding_store
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession


class EmbeddingStoreInitializationWizardController:
    """Review and explicitly initialize the embedding store schema."""

    spec = WizardSpec(
        key="groundworkers.embedding-store-initialize",
        title="Initialize embedding store",
        purpose="Create the registry and backend prerequisites required for embeddings.",
        apply_label="Initialize",
    )

    def __init__(self, session: SetupSession) -> None:
        self._session = session

    def start(self) -> WizardSnapshot:
        return self._snapshot()

    def review(self) -> WizardTransition:
        return WizardTransition(self._snapshot())

    def submit(self, _values: Mapping[str, object]) -> WizardTransition:
        return WizardTransition(self._snapshot())

    def back(self) -> WizardSnapshot:
        return self._snapshot()

    def apply(self) -> WizardResult:
        result = initialize_embedding_store(self._session.configuration)
        if not result.reachable:
            detail = result.failure.detail if result.failure is not None else None
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="The embedding store could not be initialized.",
                detail=detail,
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        self._session.refresh_configuration()
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary="Embedding store initialized.",
            detail="The registry and backend prerequisites are ready for inspection and population.",
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def cancel(self) -> WizardResult:
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary="Embedding store initialization cancelled. Nothing was changed.",
        )

    def _snapshot(self) -> WizardSnapshot:
        return WizardSnapshot(
            spec=self.spec,
            step=ReviewStep(
                key="review",
                title="Review initialization",
                review=WizardReview(
                    changes=(),
                    effects=(
                        "Creates the omop-emb registry schema and backend prerequisites.",
                    ),
                    warnings=(
                        "This is the only setup action that performs embedding-store DDL.",
                    ),
                    ready_to_apply=True,
                ),
            ),
            step_index=0,
            step_count=1,
            values={},
            can_back=False,
            can_next=False,
            can_apply=True,
            expected_revision=self._session.configuration.revision,
        )


__all__ = ["EmbeddingStoreInitializationWizardController"]
