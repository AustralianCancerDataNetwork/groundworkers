"""Generic wizard for plugin-owned setup plans."""

from __future__ import annotations

from collections.abc import Mapping

from groundskeeping.contracts.actions import FieldKind, FieldSpec, ValidationIssue
from groundskeeping.contracts.wizards import (
    FormStep,
    ReviewChange,
    ReviewStep,
    WizardController,
    WizardResult,
    WizardResultStatus,
    WizardReview,
    WizardSnapshot,
    WizardSpec,
    WizardTransition,
    validate_wizard_steps,
)

from groundworkers.application.setup.maintenance_runs import MaintenanceRunner
from groundworkers.plugins import PluginSetupStep
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession


class PluginSetupWizardController(WizardController):
    """Render arguments and start a plugin's durable maintenance plan."""

    def __init__(self, session: SetupSession, step: PluginSetupStep) -> None:
        self._session = session
        self._setup_step = step
        self._spec = WizardSpec(
            key=f"{step.key}.wizard",
            title=step.title,
            purpose=step.purpose,
            apply_label=step.apply_label,
        )
        self._values: dict[str, object] = {
            argument.key: argument.default
            for argument in step.arguments
            if argument.default is not None
        }
        self._step_index = 0
        validate_wizard_steps(self._steps())

    @property
    def spec(self) -> WizardSpec:
        return self._spec

    def start(self) -> WizardSnapshot:
        return self._snapshot()

    def submit(self, values: Mapping[str, object]) -> WizardTransition:
        issues: list[ValidationIssue] = []
        for field in self._fields():
            try:
                parsed = field.parse(values.get(field.key, self._values.get(field.key)))
            except ValueError as exc:
                issues.append(ValidationIssue(str(exc), field_key=field.key))
            else:
                self._values[field.key] = parsed.value
        if issues:
            return WizardTransition(self._snapshot(issues=tuple(issues)), tuple(issues))
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
            plan = self._setup_step.build_plan(
                self._values,
                str(self._session.configuration.path),
            )
            run = MaintenanceRunner().start(plan)
        except Exception as exc:
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary=f"{self._setup_step.title} did not start.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary=f"{self._setup_step.title} run {run.run_id} started.",
            detail=(
                f"{run.total} ordered step(s) are persisted under {run.root}. "
                "Inspect progress in the Runs section."
            ),
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def cancel(self) -> WizardResult:
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary=f"{self._setup_step.title} cancelled. Nothing was changed.",
        )

    def _fields(self) -> tuple[FieldSpec, ...]:
        return tuple(
            FieldSpec(
                key=argument.key,
                label=argument.label,
                kind=FieldKind(argument.kind),
                required=argument.required,
                default=argument.default,
                help=argument.help,
            )
            for argument in self._setup_step.arguments
        )

    def _steps(self) -> tuple[FormStep | ReviewStep, ...]:
        return (
            FormStep(
                key="arguments",
                title="Setup arguments",
                purpose="These values are used for this run and are not saved as plugin configuration.",
                fields=self._fields(),
            ),
            ReviewStep(
                key="review",
                title="Review and run",
                review=WizardReview(
                    changes=tuple(
                        ReviewChange(
                            field=argument.label,
                            before=argument.default,
                            after=self._values.get(argument.key),
                        )
                        for argument in self._setup_step.arguments
                    ),
                    effects=("The operation will run in the background with a persisted log.",),
                ),
            ),
        )

    def _snapshot(self, *, issues: tuple[ValidationIssue, ...] = ()) -> WizardSnapshot:
        steps = self._steps()
        step = steps[self._step_index]
        values = {} if isinstance(step, ReviewStep) else dict(self._values)
        return WizardSnapshot(
            spec=self.spec,
            step=step,
            step_index=self._step_index,
            step_count=len(steps),
            values=values,
            issues=issues,
            can_back=self._step_index > 0,
            can_next=isinstance(step, FormStep),
            can_review=True,
            can_apply=isinstance(step, ReviewStep) and not issues,
            expected_revision=self._session.configuration.revision,
        )


__all__ = ["PluginSetupWizardController"]
