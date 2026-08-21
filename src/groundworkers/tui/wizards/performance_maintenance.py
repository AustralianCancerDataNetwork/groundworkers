from __future__ import annotations

from collections.abc import Callable, Mapping

from groundskeeping.contracts.actions import FieldKind, FieldSpec, ValidationIssue
from groundskeeping.contracts.wizards import (
    FormStep,
    ReviewChange,
    ReviewStep,
    WizardResult,
    WizardResultStatus,
    WizardReview,
    WizardSnapshot,
    WizardSpec,
    WizardTransition,
    validate_wizard_steps,
)

from groundworkers.application.setup.maintenance_runs import MaintenanceRun
from groundworkers.application.setup.models import EmbeddingCoverageReport
from groundworkers.application.setup.performance_maintenance import (
    PerformanceRemediation,
    build_performance_commands,
    start_performance_run,
)
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession

_FIELD_BY_REMEDIATION = {
    PerformanceRemediation.TRIGRAM_INDEXES: "create_trigram_indexes",
    PerformanceRemediation.EMBEDDING_INDEX: "create_embedding_index",
}
_LABELS = {
    PerformanceRemediation.TRIGRAM_INDEXES: "Create Groundworkers trigram indexes",
    PerformanceRemediation.EMBEDDING_INDEX: "Build the embedding vector index",
}


class PerformanceMaintenanceWizardController:
    """Review and start supported non-graph performance index maintenance."""

    spec = WizardSpec(
        key="groundworkers.performance-maintenance",
        title="Prepare performance indexes",
        purpose="Build optional indexes that improve Groundworkers search performance.",
        apply_label="Run",
    )

    def __init__(
        self,
        session: SetupSession,
        *,
        embedding_coverage: EmbeddingCoverageReport | None,
        trigram_available: bool,
        launcher: Callable[[tuple[object, ...]], MaintenanceRun] | None = None,
    ) -> None:
        self._session = session
        self._coverage = embedding_coverage
        self._trigram_available = trigram_available
        self._launcher = launcher or (
            lambda commands: start_performance_run(
                commands,
                resource_key=f"performance:{session.configuration.path}",
            )
        )
        self._selected: set[PerformanceRemediation] = set()
        if trigram_available:
            self._selected.add(PerformanceRemediation.TRIGRAM_INDEXES)
        if self._embedding_available:
            self._selected.add(PerformanceRemediation.EMBEDDING_INDEX)
        self._step_index = 0
        validate_wizard_steps(self._steps())

    @property
    def _embedding_available(self) -> bool:
        return bool(
            self._coverage is not None
            and self._coverage.coverage.available
            and self._coverage.index.registered
            and self._coverage.configuration.backend == "pgvector"
        )

    def start(self) -> WizardSnapshot:
        return self._snapshot()

    def submit(self, values: Mapping[str, object]) -> WizardTransition:
        step = self._steps()[self._step_index]
        if isinstance(step, FormStep):
            issues = self._submit_form(values)
            if issues:
                return WizardTransition(self._snapshot(issues=issues), issues)
        self._step_index = min(self._step_index + 1, len(self._steps()) - 1)
        return WizardTransition(self._snapshot())

    def back(self) -> WizardSnapshot:
        self._step_index = max(0, self._step_index - 1)
        return self._snapshot()

    def review(self) -> WizardTransition:
        self._step_index = len(self._steps()) - 1
        return WizardTransition(self._snapshot())

    def apply(self) -> WizardResult:
        try:
            commands = build_performance_commands(
                tuple(remediation for remediation in PerformanceRemediation if remediation in self._selected),
                embedding_model=(self._coverage.index.model_name if self._coverage else None),
                config_path=self._session.configuration.path,
            )
            if not commands:
                raise ValueError("Nothing is selected to run.")
            launch = self._launcher(commands)
        except Exception as exc:
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="Performance preparation did not start.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary=f"Performance preparation run {launch.run_id} started.",
            detail=(
                f"{launch.total} ordered step(s) are persisted under {launch.root}. "
                "Inspect progress in Runs."
            ),
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def cancel(self) -> WizardResult:
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary="Performance preparation cancelled. Nothing was changed.",
        )

    def _steps(self) -> tuple[FormStep | ReviewStep, ...]:
        return (self._actions_step(), self._review_step())

    def _actions_step(self) -> FormStep:
        return FormStep(
            key="actions",
            title="What to prepare",
            purpose="Selections start from the performance checks available in this setup.",
            fields=tuple(
                FieldSpec(
                    key,
                    _LABELS[remediation],
                    kind=FieldKind.BOOLEAN,
                    default=remediation in self._selected,
                    disabled=(
                        remediation is PerformanceRemediation.TRIGRAM_INDEXES
                        and not self._trigram_available
                    )
                    or (
                        remediation is PerformanceRemediation.EMBEDDING_INDEX
                        and not self._embedding_available
                    ),
                    help=(
                        "Requires a PostgreSQL CDM database."
                        if remediation is PerformanceRemediation.TRIGRAM_INDEXES
                        else "Requires a registered pgvector model."
                    ),
                )
                for remediation, key in _FIELD_BY_REMEDIATION.items()
            ),
        )

    def _review_step(self) -> ReviewStep:
        return ReviewStep(
            key="review",
            title="Confirm",
            review=WizardReview(
                changes=tuple(
                    ReviewChange(field=_LABELS[item], before="available", after="will run")
                    for item in PerformanceRemediation
                    if item in self._selected
                ),
                effects=("Each index build runs as a background command with its own log.",),
                warnings=(
                    "Index builds can be slow and temporarily use substantial database resources.",
                ),
                ready_to_apply=bool(self._selected),
            ),
        )

    def _snapshot(self, *, issues: tuple[ValidationIssue, ...] = ()) -> WizardSnapshot:
        steps = self._steps()
        self._step_index = min(self._step_index, len(steps) - 1)
        step = steps[self._step_index]
        return WizardSnapshot(
            spec=self.spec,
            step=step,
            step_index=self._step_index,
            step_count=len(steps),
            values={} if isinstance(step, ReviewStep) else {
                key: remediation in self._selected
                for remediation, key in _FIELD_BY_REMEDIATION.items()
            },
            issues=issues,
            can_back=self._step_index > 0,
            can_next=not isinstance(step, ReviewStep),
            can_apply=isinstance(step, ReviewStep) and step.review.ready_to_apply and not issues,
            expected_revision=self._session.configuration.revision,
        )

    def _submit_form(self, values: Mapping[str, object]) -> tuple[ValidationIssue, ...]:
        for remediation, key in _FIELD_BY_REMEDIATION.items():
            if _as_bool(values.get(key)):
                self._selected.add(remediation)
            else:
                self._selected.discard(remediation)
        if not self._selected:
            return (ValidationIssue("Select at least one index to prepare.", "create_trigram_indexes"),)
        return ()


def _as_bool(value: object) -> bool:
    return (isinstance(value, bool) and value) or str(value).strip().lower() in {"true", "yes", "1", "on"}


__all__ = ["PerformanceMaintenanceWizardController"]
