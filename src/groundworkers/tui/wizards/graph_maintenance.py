from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from groundskeeping.contracts.actions import (
    ChoiceOption,
    FieldKind,
    FieldSpec,
    ValidationIssue,
)
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

from groundworkers.application.setup.graph_maintenance import (
    PREDICATE_CSV_NAMES,
    GraphRemediation,
    GraphRemediationRequest,
    build_graph_remediation_commands,
    outstanding_remediations,
    packaged_predicate_csv_dir,
    start_graph_remediation_run,
)
from groundworkers.application.setup.models import ConnectionResult
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession

_FIELD_BY_REMEDIATION: dict[GraphRemediation, str] = {
    GraphRemediation.RELATIONSHIP_CLASSIFICATION: "load_relationship_classification",
    GraphRemediation.FULLTEXT_INDEXES: "create_fulltext_indexes",
    GraphRemediation.FUNCTIONAL_INDEXES: "create_functional_indexes",
}
_LABELS: dict[GraphRemediation, str] = {
    GraphRemediation.RELATIONSHIP_CLASSIFICATION: "Load relationship-classification tables",
    GraphRemediation.FULLTEXT_INDEXES: "Create full-text indexes",
    GraphRemediation.FUNCTIONAL_INDEXES: "Create functional text indexes",
}

_SOURCE_BUNDLED = "bundled"
_SOURCE_CUSTOM = "custom"


class GraphMaintenanceWizardController:
    """Close the gaps the graph readiness check reports.

    Each remediation is a sibling package's own CLI command run out-of-process,
    the same way embedding population already works: the DDL involved (GIN index
    builds, tsvector population, CLUSTER over vocabulary tables) is far too slow
    to hold the console, and its output belongs in a log the operator can read.

    Selections default to exactly what the readiness check found outstanding, so
    the operator confirms a diagnosis rather than repeating it.
    """

    spec = WizardSpec(
        key="groundworkers.graph-maintenance",
        title="Prepare the graph",
        purpose="Load the relationship tables and build the indexes grounding needs.",
        apply_label="Run",
    )

    def __init__(
        self,
        session: SetupSession,
        readiness: ConnectionResult | None,
    ) -> None:
        self._session = session
        self._readiness = readiness
        self._outstanding = outstanding_remediations(readiness)
        self._selected: set[GraphRemediation] = set(self._outstanding)
        # Pre-filled from the copy bundled with Groundworkers while omop-graph's
        # own CSVs stay outside its wheel. Still a prompt rather than a silent
        # default: a site may maintain its own classification.
        self._bundled_csv_dir: Path | None = packaged_predicate_csv_dir()
        self._csv_dir: Path | None = self._bundled_csv_dir
        self._use_bundled = self._bundled_csv_dir is not None
        self._cluster = True
        self._step_index = 0
        validate_wizard_steps(self._steps())

    def start(self) -> WizardSnapshot:
        return self._wizard_snapshot()

    def submit(self, values: Mapping[str, object]) -> WizardTransition:
        step = self._steps()[self._step_index]
        issues = self._submit_form(step, values) if isinstance(step, FormStep) else ()
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
            commands = build_graph_remediation_commands(
                self._request(), config_path=self._session.configuration.path
            )
        except ValueError as exc:
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="The graph preparation could not be started.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        if not commands:
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="Nothing was selected to run.",
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        try:
            launches = start_graph_remediation_run(
                commands,
                resource_key=f"graph:{self._session.configuration.path}",
            )
        except Exception as exc:
            # Broad except: rendered as setup failure.
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="The graph preparation did not start.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary=f"Graph preparation run {launches.run_id} started.",
            detail=(
                f"{launches.total} ordered step(s) are persisted under "
                f"{launches.root}. Reopen the setup console to inspect progress."
            ),
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def cancel(self) -> WizardResult:
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary="Graph preparation cancelled. Nothing was changed.",
        )

    # -- steps ---------------------------------------------------------------

    def _steps(self) -> tuple[FormStep | ReviewStep, ...]:
        steps: list[FormStep | ReviewStep] = [self._actions_step()]
        if GraphRemediation.RELATIONSHIP_CLASSIFICATION in self._selected:
            steps.append(self._sources_step())
            if not self._use_bundled:
                steps.append(self._custom_sources_step())
        steps.append(self._review_step())
        return tuple(steps)

    def _actions_step(self) -> FormStep:
        fields = [
            FieldSpec(
                _FIELD_BY_REMEDIATION[remediation],
                _LABELS[remediation],
                kind=FieldKind.BOOLEAN,
                default=remediation in self._selected,
                help=(
                    "Reported missing by the readiness check."
                    if remediation in self._outstanding
                    else "Already present. Selecting this re-runs it."
                ),
            )
            for remediation in GraphRemediation
        ]
        fields.append(
            FieldSpec(
                "cluster",
                "CLUSTER tables after indexing",
                kind=FieldKind.BOOLEAN,
                default=self._cluster,
                help="Rewrites the heap. Needs free disk space on large vocabularies.",
            )
        )
        return FormStep(
            key="actions",
            title="What to prepare",
            purpose="Selections start from what the readiness check found missing.",
            fields=tuple(fields),
        )

    def _sources_step(self) -> FormStep:
        """Ask which classification to load, not where it lives.

        The bundled copy is the answer in almost every case, and its absolute
        path is far too long to read in a single-line field. Only an operator
        who keeps their own classification needs to type anything.
        """
        if self._bundled_csv_dir is None:
            choices = (ChoiceOption(_SOURCE_CUSTOM, "Choose a directory"),)
            default = _SOURCE_CUSTOM
            help_text = (
                "No classification is bundled with this install, so a directory "
                "is required."
            )
        else:
            choices = (
                ChoiceOption(
                    _SOURCE_BUNDLED, "Use the classification bundled with Groundworkers"
                ),
                ChoiceOption(_SOURCE_CUSTOM, "Use a different directory"),
            )
            default = _SOURCE_BUNDLED if self._use_bundled else _SOURCE_CUSTOM
            help_text = f"Bundled copy: {self._bundled_csv_dir}"
        return FormStep(
            key="sources",
            title="Predicate classification",
            purpose="omop-graph loads these tables from CSV.",
            fields=(
                FieldSpec(
                    "predicate_source",
                    "Classification",
                    kind=FieldKind.CHOICE,
                    default=default,
                    choices=choices,
                    disabled=self._bundled_csv_dir is None,
                    help=help_text,
                ),
            ),
        )

    def _custom_sources_step(self) -> FormStep:
        return FormStep(
            key="custom_sources",
            title="Classification directory",
            purpose="Where your own predicate CSVs live.",
            fields=(
                FieldSpec(
                    "predicate_csv_dir",
                    "CSV directory",
                    kind=FieldKind.EXISTING_PATH,
                    default=str(self._csv_dir) if self._csv_dir else None,
                    help="Must contain predicate_classification.csv and predicate_mapping.csv.",
                ),
            ),
        )

    def _review_step(self) -> ReviewStep:
        changes = tuple(
            ReviewChange(
                field=_LABELS[remediation],
                before="outstanding" if remediation in self._outstanding else "present",
                after="will run",
            )
            for remediation in GraphRemediation
            if remediation in self._selected
        )
        warnings: list[str] = []
        if not self._selected:
            warnings.append("Nothing is selected, so nothing will run.")
        if self._cluster and GraphRemediation.FUNCTIONAL_INDEXES in self._selected:
            warnings.append(
                "CLUSTER rewrites each table. On a full vocabulary this is slow and "
                "needs free disk space."
            )
        if GraphRemediation.FULLTEXT_INDEXES in self._selected:
            warnings.append(
                "Full-text preparation runs install then populate; the indexes are "
                "not usable until both finish."
            )
        return ReviewStep(
            key="review",
            title="Confirm",
            review=WizardReview(
                changes=changes,
                effects=("Each step runs as a background command with its own log.",),
                warnings=tuple(warnings),
                ready_to_apply=bool(self._selected),
            ),
        )

    # -- internals -----------------------------------------------------------

    def _request(self) -> GraphRemediationRequest:
        return GraphRemediationRequest(
            actions=tuple(r for r in GraphRemediation if r in self._selected),
            predicate_csv_dir=self._csv_dir,
            cluster=self._cluster,
        )

    def _submit_form(
        self, step: FormStep, values: Mapping[str, object]
    ) -> tuple[ValidationIssue, ...]:
        if step.key == "actions":
            for remediation, key in _FIELD_BY_REMEDIATION.items():
                if _as_bool(values.get(key)):
                    self._selected.add(remediation)
                else:
                    self._selected.discard(remediation)
            self._cluster = _as_bool(values.get("cluster"))
            if not self._selected:
                return (
                    ValidationIssue(
                        "Select at least one thing to prepare.",
                        field_key=_FIELD_BY_REMEDIATION[
                            GraphRemediation.RELATIONSHIP_CLASSIFICATION
                        ],
                    ),
                )
            return ()
        if step.key == "sources":
            choice = str(values.get("predicate_source") or "").strip()
            self._use_bundled = choice == _SOURCE_BUNDLED
            if self._use_bundled:
                self._csv_dir = self._bundled_csv_dir
            return ()
        if step.key == "custom_sources":
            raw = str(values.get("predicate_csv_dir") or "").strip()
            if not raw:
                return (
                    ValidationIssue(
                        "A CSV directory is required.", field_key="predicate_csv_dir"
                    ),
                )
            candidate = Path(raw).expanduser()
            if not candidate.is_dir():
                return (
                    ValidationIssue(
                        "That path is not a directory.", field_key="predicate_csv_dir"
                    ),
                )
            missing = [
                name for name in PREDICATE_CSV_NAMES if not (candidate / name).is_file()
            ]
            if missing:
                return (
                    ValidationIssue(
                        f"Missing in that directory: {', '.join(missing)}.",
                        field_key="predicate_csv_dir",
                    ),
                )
            self._csv_dir = candidate
            return ()
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
            values=self._safe_values(step),
            issues=issues,
            can_back=self._step_index > 0,
            can_next=not isinstance(step, ReviewStep),
            can_apply=isinstance(step, ReviewStep)
            and step.review.ready_to_apply
            and not issues,
            expected_revision=self._session.configuration.revision,
        )

    def _safe_values(self, step: FormStep | ReviewStep) -> Mapping[str, object]:
        if isinstance(step, ReviewStep):
            return {}
        if step.key == "sources":
            return {
                "predicate_source": (
                    _SOURCE_BUNDLED if self._use_bundled else _SOURCE_CUSTOM
                )
            }
        if step.key == "custom_sources":
            return {"predicate_csv_dir": str(self._csv_dir) if self._csv_dir else ""}
        values: dict[str, object] = {
            key: remediation in self._selected
            for remediation, key in _FIELD_BY_REMEDIATION.items()
        }
        values["cluster"] = self._cluster
        return values


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1", "on"}
