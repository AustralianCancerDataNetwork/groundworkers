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
from oa_configurator import DEFAULT_CONFIG_PATH  # type: ignore[import-untyped]

from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession


class ConfigLocationWizardController:
    """Choose where the stack configuration lives, before anything is written.

    Deliberately the smallest possible wizard: it settles a path and nothing
    else. Every actual configuration journey -- the CDM database, the embedding
    model, the chat model -- already exists behind the setup page's own
    workflows, and this hands straight back to them once the location is known.

    It exists because the setup console previously assumed the default location
    silently. An operator with a config somewhere else had no way to say so from
    inside the TUI, and one who wanted it somewhere else could only find out
    after the first write had already gone to the default path.
    """

    spec = WizardSpec(
        key="groundworkers.config-location",
        title="Choose the configuration location",
        purpose="Accept the default location or point Groundworkers at an existing file.",
        # Kept short deliberately: groundskeeping fixes the wizard buttons at
        # width 14, so a longer label wraps to two lines and breaks the row's
        # alignment. The other buttons are 4-6 characters.
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
        self._default = Path(
            default_path or session.config_path or DEFAULT_CONFIG_PATH
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
        self._session.refresh_configuration()
        snapshot = self._session.configuration
        if snapshot.usable:
            summary = f"Loaded the configuration at {self._selected}."
        else:
            summary = f"Groundworkers will create its configuration at {self._selected}."
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary=summary,
            detail="Continue in the Database section to configure the CDM connection.",
            refresh_pages=frozenset({SETUP_ROUTE.key}),
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
                    help="Accept the default, or give the path to an existing config.toml.",
                ),
            ),
        )

    def _review_step(self) -> ReviewStep:
        exists = self._selected.is_file()
        warnings: list[str] = []
        if not exists and not self._selected.parent.exists():
            warnings.append(
                f"{self._selected.parent} does not exist yet and will be created "
                "when the configuration is first saved."
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
                    "Load the existing configuration."
                    if exists
                    else "Start a new configuration at this path.",
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
