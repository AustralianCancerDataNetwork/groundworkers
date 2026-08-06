from __future__ import annotations

from collections.abc import Mapping
import re

from groundskeeping.contracts import (
    Choice,
    ChoiceOption,
    ChoiceStep,
    FieldKind,
    FieldSpec,
    FormStep,
    ReviewChange,
    ReviewStep,
    SemanticStatus,
    ValidationIssue,
    WizardResult,
    WizardResultStatus,
    WizardReview,
    WizardSnapshot,
    WizardSpec,
    WizardTransition,
    validate_wizard_steps,
)

from groundworkers.application.setup.database_configuration import (
    SUPPORTED_DIALECTS,
    ConnectionStrategy,
    DatabaseConfigurationDraft,
    apply_database_configuration,
    draft_from_plan,
    plan_database_configuration,
    update_draft,
)
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession

_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


class DatabaseConfigurationWizardController:
    """Current-schema database setup wizard for the Groundworkers TUI."""

    spec = WizardSpec(
        key="groundworkers.database-configuration",
        title="Configure database",
        purpose="Create or update the OMOP database resource Groundworkers uses.",
        apply_label="Save",
    )

    def __init__(self, session: SetupSession) -> None:
        self._session = session
        self._snapshot = session.configuration
        self._plan = plan_database_configuration(self._snapshot)
        self._draft = draft_from_plan(self._plan, self._snapshot)
        self._display_values = self._draft.safe_for_display()
        self._step_index = 0
        validate_wizard_steps(self._steps())

    def start(self) -> WizardSnapshot:
        return self._wizard_snapshot()

    def submit(self, values: Mapping[str, object]) -> WizardTransition:
        step = self._steps()[self._step_index]
        if isinstance(step, ChoiceStep):
            issues = self._submit_choice(step, values)
        elif isinstance(step, FormStep):
            issues = self._submit_form(step, values)
        else:
            issues = ()
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
        try:
            apply_database_configuration(self._snapshot, self._draft)
        except PermissionError as exc:
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="Database configuration is read-only.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        except RuntimeError as exc:
            return WizardResult(
                status=WizardResultStatus.CONFLICTED,
                summary="Configuration changed before save.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        except Exception as exc:  # noqa: BLE001 - rendered as a safe setup failure
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="Database configuration was not saved.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        self._session.refresh_configuration()
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary="Database configuration saved.",
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def cancel(self) -> WizardResult:
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary="Database configuration cancelled.",
        )

    def _steps(self) -> tuple[ChoiceStep | FormStep | ReviewStep, ...]:
        return (
            self._ownership_step(),
            self._identity_step(),
            self._strategy_step(),
            self._connection_step(),
            self._schema_step(),
            self._review_step(),
        )

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
            values=self._safe_values(step),
            issues=issues,
            can_back=self._step_index > 0,
            can_next=not isinstance(step, ReviewStep),
            can_apply=isinstance(step, ReviewStep)
            and step.review.ready_to_apply
            and not issues,
            expected_revision=self._snapshot.revision,
        )

    def _safe_values(
        self, step: ChoiceStep | FormStep | ReviewStep
    ) -> Mapping[str, object]:
        if isinstance(step, ChoiceStep):
            return {step.key: self._display_values.get(step.key)}
        if isinstance(step, FormStep):
            values: dict[str, object] = {}
            for field in step.fields:
                if field.masks_value:
                    values[field.key] = None
                    continue
                value = self._display_values.get(field.key, field.default)
                if (
                    field.kind is FieldKind.CHOICE
                    and not field.required
                    and value is None
                    and any(choice.value == "" for choice in field.choices)
                ):
                    value = ""
                values[field.key] = value
            return values
        return {}

    def _submit_choice(
        self, step: ChoiceStep, values: Mapping[str, object]
    ) -> tuple[ValidationIssue, ...]:
        selected = values.get(step.key)
        allowed = {choice.key for choice in step.choices}
        if not isinstance(selected, str) or selected not in allowed:
            return (
                ValidationIssue("Choose one setup path.", field_key=step.key),
            )
        changes: dict[str, object] = {"connection_strategy": selected}
        if selected == ConnectionStrategy.CLONE.value:
            changes["source_connection_name"] = self._plan.connection_name
            changes["connection_name"] = self._unique_connection_name(
                f"{self._plan.connection_name}_groundworkers"
            )
        elif selected == ConnectionStrategy.CREATE.value and self._plan.connection_names:
            changes["source_connection_name"] = None
            changes["connection_name"] = self._unique_connection_name(
                "groundworkers_main"
            )
        elif selected in {
            ConnectionStrategy.REUSE.value,
            ConnectionStrategy.EDIT.value,
        }:
            changes["source_connection_name"] = self._plan.connection_name
            changes["connection_name"] = self._plan.connection_name
        self._draft = update_draft(self._draft, **changes)
        self._display_values.update(self._draft.safe_for_display())
        return ()

    def _submit_form(
        self, step: FormStep, values: Mapping[str, object]
    ) -> tuple[ValidationIssue, ...]:
        if step.key == "ownership" and not self._plan.editable:
            return (
                ValidationIssue(
                    self._plan.read_only_reason
                    or "This configuration cannot be edited here.",
                    status=SemanticStatus.ERROR,
                ),
            )
        parsed: dict[str, object] = {}
        issues: list[ValidationIssue] = []
        for field in step.fields:
            try:
                parsed[field.key] = field.parse(values.get(field.key)).value
            except ValueError as exc:
                issues.append(ValidationIssue(str(exc), field_key=field.key))
        if issues:
            return tuple(issues)
        if step.key == "identity":
            name = str(parsed["resource_name"]).strip()
            if not _NAME_PATTERN.match(name):
                return (
                    ValidationIssue(
                        "Use letters, numbers, dot, dash or underscore.",
                        field_key="resource_name",
                    ),
                )
            self._draft = update_draft(self._draft, resource_name=name)
        elif step.key == "connection":
            try:
                self._draft = self._draft_from_connection_values(parsed)
            except ValueError as exc:
                return (ValidationIssue(str(exc), field_key="connection_name"),)
        elif step.key == "schemas":
            self._draft = update_draft(
                self._draft,
                cdm_schema=str(parsed["cdm_schema"]).strip(),
                vocabulary_connection_name=parsed.get("vocabulary_connection_name"),
                vocabulary_schema=parsed.get("vocabulary_schema"),
                results_schema=parsed.get("results_schema"),
            )
        self._display_values.update(self._draft.safe_for_display())
        return ()

    def _draft_from_connection_values(
        self, parsed: Mapping[str, object]
    ) -> DatabaseConfigurationDraft:
        if self._draft.connection_strategy is ConnectionStrategy.REUSE:
            return update_draft(
                self._draft,
                connection_name=str(parsed["connection_name"]),
                source_connection_name=str(parsed["connection_name"]),
            )

        clear_password = bool(parsed.get("clear_password"))
        password = parsed.get("password")
        password_text = str(password) if password not in (None, "") else None
        password_action = "preserve"
        if clear_password:
            password_action = "clear"
        elif password_text:
            password_action = "set"

        connection_name = str(parsed["connection_name"]).strip()
        if not _NAME_PATTERN.match(connection_name):
            raise ValueError("Connection name must use letters, numbers, dot, dash or underscore.")
        return update_draft(
            self._draft,
            connection_name=connection_name,
            dialect=str(parsed["dialect"]),
            host=parsed.get("host"),
            port=parsed.get("port"),
            user=parsed.get("user"),
            password=password_text,
            password_action=password_action,
            database_name=parsed.get("database_name"),
            read_only=bool(parsed.get("read_only")),
            test_only=bool(parsed.get("test_only")),
        )

    def _ownership_step(self) -> FormStep:
        return FormStep(
            key="ownership",
            title="Configuration ownership",
            purpose="Confirm the file this setup wizard will edit.",
            fields=(
                FieldSpec("path", "Path", default=self._plan.path, read_only=True),
                FieldSpec(
                    "revision",
                    "Revision",
                    default=self._plan.revision or "new file",
                    read_only=True,
                ),
                FieldSpec(
                    "source",
                    "Source",
                    default=self._snapshot.ownership.source_label,
                    read_only=True,
                ),
            ),
        )

    def _identity_step(self) -> FormStep:
        return FormStep(
            key="identity",
            title="OMOP database identity",
            purpose="Name the logical OMOP database mapping Groundworkers should use.",
            fields=(
                FieldSpec(
                    "resource_name",
                    "OMOP database",
                    default=self._draft.resource_name,
                    help="This is the logical resource name, not the physical host.",
                ),
            ),
        )

    def _strategy_step(self) -> ChoiceStep:
        choices = [
            Choice(
                ConnectionStrategy.CREATE.value,
                "Create connection",
                "Add a new physical connection for this OMOP database.",
            )
        ]
        if self._plan.connection_names:
            choices.insert(
                0,
                Choice(
                    ConnectionStrategy.REUSE.value,
                    "Reuse connection",
                    "Point the OMOP database mapping at an existing connection.",
                ),
            )
            choices.append(
                Choice(
                    ConnectionStrategy.EDIT.value,
                    "Edit connection",
                    "Update the selected physical connection in place.",
                )
            )
        if self._plan.shared_connection:
            choices.append(
                Choice(
                    ConnectionStrategy.CLONE.value,
                    "Clone connection",
                    "Copy the current connection and repoint only Groundworkers.",
                )
            )
        return ChoiceStep(
            key="connection_strategy",
            title="Connection strategy",
            purpose="Choose how the logical OMOP database should reach data.",
            choices=tuple(choices),
        )

    def _connection_step(self) -> FormStep:
        if self._draft.connection_strategy is ConnectionStrategy.REUSE:
            return FormStep(
                key="connection",
                title="Reuse connection",
                fields=(
                    FieldSpec(
                        "connection_name",
                        "Connection",
                        kind=FieldKind.CHOICE,
                        choices=tuple(
                            ChoiceOption(value=name, label=name)
                            for name in self._plan.connection_names
                        ),
                        default=self._draft.connection_name,
                    ),
                ),
            )
        return FormStep(
            key="connection",
            title="Connection details",
            purpose="Blank passwords preserve the existing secret unless clear is selected.",
            fields=(
                FieldSpec(
                    "connection_name",
                    "Connection name",
                    default=self._draft.connection_name,
                ),
                FieldSpec(
                    "dialect",
                    "Dialect",
                    kind=FieldKind.CHOICE,
                    choices=tuple(
                        ChoiceOption(value=dialect, label=dialect)
                        for dialect in SUPPORTED_DIALECTS
                    ),
                    default=self._draft.dialect,
                ),
                FieldSpec("host", "Host", required=False, default=self._draft.host),
                FieldSpec(
                    "port",
                    "Port",
                    kind=FieldKind.INTEGER,
                    required=False,
                    default=self._draft.port,
                ),
                FieldSpec("user", "User", required=False, default=self._draft.user),
                FieldSpec(
                    "password",
                    "Password",
                    kind=FieldKind.SECRET,
                    required=False,
                    secret_clearable=True,
                ),
                FieldSpec(
                    "clear_password",
                    "Clear password",
                    kind=FieldKind.BOOLEAN,
                    required=False,
                    default=False,
                ),
                FieldSpec(
                    "database_name",
                    "Database name or path",
                    required=False,
                    default=self._draft.database_name,
                ),
                FieldSpec(
                    "read_only",
                    "Read only",
                    kind=FieldKind.BOOLEAN,
                    required=False,
                    default=self._draft.read_only,
                ),
                FieldSpec(
                    "test_only",
                    "Test only",
                    kind=FieldKind.BOOLEAN,
                    required=False,
                    default=self._draft.test_only,
                ),
            ),
        )

    def _schema_step(self) -> FormStep:
        return FormStep(
            key="schemas",
            title="OMOP schema mapping",
            fields=(
                FieldSpec("cdm_schema", "CDM schema", default=self._draft.cdm_schema),
                FieldSpec(
                    "vocabulary_connection_name",
                    "Vocabulary connection",
                    kind=FieldKind.CHOICE,
                    required=False,
                    default=self._draft.vocabulary_connection_name,
                    choices=tuple(
                        [ChoiceOption(value="", label="Use primary connection")]
                        + [
                            ChoiceOption(value=name, label=name)
                            for name in self._plan.connection_names
                        ]
                    ),
                ),
                FieldSpec(
                    "vocabulary_schema",
                    "Vocabulary schema",
                    required=False,
                    default=self._draft.vocabulary_schema,
                    help="Falls back to the CDM schema when blank.",
                ),
                FieldSpec(
                    "results_schema",
                    "Results schema",
                    required=False,
                    default=self._draft.results_schema,
                ),
            ),
        )

    def _review_step(self) -> ReviewStep:
        effects = ["Groundworkers will use this OMOP database after restart."]
        if self._plan.shared_connection and self._draft.connection_strategy is ConnectionStrategy.EDIT:
            effects.append(
                "Editing this shared connection affects: "
                + ", ".join(
                    f"{item.resource_name} ({item.role})"
                    for item in self._plan.shared_connection_references
                )
            )
        return ReviewStep(
            key="review",
            title="Review and apply",
            review=WizardReview(
                changes=tuple(self._review_changes()),
                effects=tuple(effects),
                warnings=(
                    ("Connection testing remains available as a separate Database action.",)
                ),
                ready_to_apply=self._plan.editable,
            ),
        )

    def _review_changes(self) -> list[ReviewChange]:
        before = self._plan.target
        changes = [
            ReviewChange(
                "OMOP database",
                before.resource_name if before is not None else None,
                self._draft.resource_name,
            ),
            ReviewChange(
                "connection",
                before.database_name if before is not None else None,
                self._draft.connection_name,
            ),
            ReviewChange(
                "CDM schema",
                before.cdm_schema if before is not None else None,
                self._draft.cdm_schema,
            ),
            ReviewChange(
                "vocabulary schema",
                before.vocabulary_schema if before is not None else None,
                self._draft.vocabulary_schema or self._draft.cdm_schema,
            ),
        ]
        if self._draft.password:
            changes.append(ReviewChange("password", None, "configured", sensitive=True))
        return changes

    def _unique_connection_name(self, stem: str) -> str:
        if stem not in self._plan.connection_names:
            return stem
        index = 2
        while f"{stem}_{index}" in self._plan.connection_names:
            index += 1
        return f"{stem}_{index}"
