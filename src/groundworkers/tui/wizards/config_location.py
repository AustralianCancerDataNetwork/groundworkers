from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from groundskeeping.contracts.actions import FieldSpec, ValidationIssue
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

from groundworkers._env import (
    ENV_CONFIG_PATH,
    env_file_path,
    rejected_config_path,
)
from groundworkers.application.setup.configuration import resolved_config_path
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession


class ConfigLocationWizardController:
    """Choose the stack configuration path before writing any settings."""

    spec = WizardSpec(
        key="groundworkers.config-location",
        title="Choose the configuration location",
        purpose="Accept the default location or point Groundworkers at an existing file.",
        apply_label="Confirm",
    )

    def __init__(
        self,
        session: SetupSession,
        *,
        default_path: str | Path | None = None,
    ) -> None:
        self._session = session
        self._original = session.configuration.path
        self._resolved = resolved_config_path()
        self._rejected = rejected_config_path()
        self._default = Path(
            default_path or session.config_path or self._rejected or self._resolved
        ).expanduser()
        self._selected = self._default
        self._step_index = 0
        validate_wizard_steps(self._steps())

    def start(self) -> WizardSnapshot:
        return self._wizard_snapshot()

    def submit(self, values: Mapping[str, object]) -> WizardTransition:
        step = self._steps()[self._step_index]
        issues = self._submit_form(values) if isinstance(step, FormStep) else ()
        if issues:
            return WizardTransition(self._wizard_snapshot(issues=issues), issues)
        self._step_index = min(self._step_index + 1, len(self._steps()) - 1)
        return WizardTransition(self._wizard_snapshot())

    def back(self) -> WizardSnapshot:
        self._step_index = max(0, self._step_index - 1)
        return self._wizard_snapshot()

    def review(self) -> WizardTransition:
        self._step_index = len(self._steps()) - 1
        return WizardTransition(self._wizard_snapshot())

    def apply(self) -> WizardResult:
        self._session.config_path = self._selected
        # The one place the console is told where the configuration lives, as
        # opposed to which file to read now. The session writes the pointer once
        # there is a configuration at that path to point at.
        self._session.record_location = not self._is_resolved_default()
        self._session.refresh_configuration()
        snapshot = self._session.configuration
        if snapshot.usable:
            summary = f"Loaded the configuration at {self._selected}."
        else:
            summary = f"Groundworkers will create its configuration at {self._selected}."
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary=summary,
            detail=self._persistence_detail(recorded=snapshot.usable),
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def _is_resolved_default(self) -> bool:
        """Whether a fresh process already resolves the selected path unaided."""

        return self._selected.expanduser().resolve() == self._resolved

    def _persistence_detail(self, *, recorded: bool) -> str:
        next_step = "Continue in the Database section to configure the CDM connection."
        if self._is_resolved_default():
            return next_step
        if recorded:
            return (
                f"Recorded in {env_file_path()}, so later runs read it too. "
                f"{next_step}"
            )
        return (
            f"{ENV_CONFIG_PATH} is recorded in {env_file_path()} once the "
            f"configuration is first saved, so later runs read it too. {next_step}"
        )

    def cancel(self) -> WizardResult:
        # The default still applies, so cancelling leaves a usable console
        # rather than a dead end.
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary=f"Keeping the current location, {self._original}.",
        )

    # -- steps ---------------------------------------------------------------

    def _steps(self) -> tuple[FormStep | ReviewStep, ...]:
        return (self._location_step(), self._review_step())

    def _location_step(self) -> FormStep:
        return FormStep(
            key="location",
            title="Configuration file",
            purpose="Where Groundworkers reads and writes its stack configuration.",
            fields=(
                FieldSpec(
                    "config_path",
                    "Configuration file",
                    default=str(self._default),
                    help=self._location_help(),
                ),
            ),
        )

    def _location_help(self) -> str:
        if self._rejected is not None:
            return (
                f"{ENV_CONFIG_PATH}: {self._rejected} does not exist - it will be created"
            )
        return (
            f"{self._resolved} is what Groundworkers resolves on its own. Another "
            f"path is recorded in {env_file_path()} so later runs find it too."
        )

    def _review_step(self) -> ReviewStep:
        exists = self._selected.is_file()
        warnings: list[str] = []
        if not exists and not self._selected.parent.exists():
            warnings.append(
                f"{self._selected.parent} does not exist yet and will be created "
                "when the configuration is first saved."
            )
        if not self._is_resolved_default():
            # Groundworkers passes OA_CONFIG_PATH to every maintenance command it
            # launches, so those follow. A stack CLI the operator runs by hand
            # does not read this file and will resolve the default.
            warnings.append(
                f"Groundworkers will read this path, and so will the maintenance "
                f"commands it launches. omop-emb, omop-alchemy and omop-graph run "
                f"by hand need {ENV_CONFIG_PATH} in their own environment."
            )
        return ReviewStep(
            key="review",
            title="Confirm the location",
            review=WizardReview(
                changes=(
                    ReviewChange(
                        field="config_path",
                        before=str(self._original),
                        after=str(self._selected),
                    ),
                ),
                effects=(
                    (
                        "Load the existing configuration."
                        if exists
                        else "Start a new configuration at this path."
                    ),
                    *(
                        ()
                        if self._is_resolved_default()
                        else (
                            f"Record {ENV_CONFIG_PATH}={self._selected} in "
                            f"{env_file_path()}, so later runs read it too.",
                        )
                    ),
                ),
                warnings=tuple(warnings),
            ),
        )

    # -- internals -----------------------------------------------------------

    def _submit_form(self, values: Mapping[str, object]) -> tuple[ValidationIssue, ...]:
        raw = str(values.get("config_path") or "").strip()
        if not raw:
            return (
                ValidationIssue("A configuration path is required.", "config_path"),
            )
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            return (
                ValidationIssue(
                    "That path is a directory. Give the path to a config.toml file.",
                    "config_path",
                ),
            )
        self._selected = candidate
        return ()

    def _wizard_snapshot(
        self, *, issues: tuple[ValidationIssue, ...] = ()
    ) -> WizardSnapshot:
        steps = self._steps()
        self._step_index = min(self._step_index, len(steps) - 1)
        step = steps[self._step_index]
        return WizardSnapshot(
            spec=self.spec,
            step=step,
            step_index=self._step_index,
            step_count=len(steps),
            values=({} if isinstance(step, ReviewStep) else {"config_path": str(self._selected)}),
            issues=issues,
            can_back=self._step_index > 0,
            can_next=not isinstance(step, ReviewStep),
            can_apply=isinstance(step, ReviewStep) and not issues,
            expected_revision=self._session.configuration.revision,
        )
