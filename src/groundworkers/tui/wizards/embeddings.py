from __future__ import annotations

from collections.abc import Callable, Mapping

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

from groundworkers.application.setup.embedding_population import (
    DEFAULT_EMBEDDING_BACKFILL_LIMIT,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    build_embedding_population_command,
    load_embedding_coverage_report,
    start_embedding_population_run,
)
from groundworkers.application.setup.maintenance_runs import MaintenanceRun
from groundworkers.application.setup.models import (
    EmbeddingCoverageReport,
    EmbeddingPopulationCommand,
    EmbeddingPopulationLaunch,
    EmbeddingPopulationRequest,
)
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession

VocabularyMode = str

SCOPE_STANDARD: str = "standard"
SCOPE_ALL: str = "all"

VOCABULARY_MODE_ALL: str = "all"
VOCABULARY_MODE_INCOMPLETE: str = "incomplete"
VOCABULARY_MODE_SELECTED: str = "selected"

INTENT_POPULATE = "populate"
INTENT_BACKFILL = "backfill"
INTENT_RECONCILE = "reconcile"


class EmbeddingPopulationWizardController:
    """Start an omop-emb concept embedding population run."""

    spec = WizardSpec(
        key="groundworkers.embedding-population",
        title="Populate embeddings",
        purpose="Choose concept filters and start the matching omop-emb command.",
        apply_label="Start",
    )

    def __init__(
        self,
        session: SetupSession,
        *,
        coverage: EmbeddingCoverageReport | None = None,
        vocabulary_mode: VocabularyMode | None = None,
        vocabularies: tuple[str, ...] | None = None,
        launcher: Callable[
            [EmbeddingPopulationCommand], EmbeddingPopulationLaunch | MaintenanceRun | None
        ]
        | None = None,
    ) -> None:
        self._session = session
        self._coverage = coverage or load_embedding_coverage_report(
            session.configuration,
            standard_only=session.embedding_standard_only,
        )
        self._intent = INTENT_BACKFILL
        self._launcher = launcher or (
            lambda command: start_embedding_population_run(
                command,
                resource_key=(
                    f"embedding:{session.configuration.path}:"
                    f"{command.argv[command.argv.index('--model-name') + 1]}"
                ),
            )
        )
        self._step_index = 0
        resolved_mode = vocabulary_mode or (
            "all" if session.embedding_vocabulary_selection_all else "selected"
        )
        resolved_vocabularies = (
            ()
            if resolved_mode == "all"
            else (vocabularies or session.embedding_selected_vocabularies)
        )
        self._request = EmbeddingPopulationRequest(
            standard_only=session.embedding_standard_only,
            vocabulary_mode=resolved_mode,
            vocabularies=resolved_vocabularies,
            limit=None,
            batch_size=DEFAULT_EMBEDDING_BATCH_SIZE,
        )
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
        if self._coverage is None:
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="Embedding coverage is unavailable.",
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        try:
            command = self._command()
            launch = self._launcher(command)
        except Exception as exc:
            # Broad except: rendered as setup failure.
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="Embedding population was not started.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        if isinstance(launch, MaintenanceRun):
            return WizardResult(
                status=WizardResultStatus.APPLIED,
                summary=f"Embedding population run {launch.run_id} started.",
                detail=(
                    f"The ordered run is persisted under {launch.root}. "
                    "Reopen the setup console to inspect progress."
                ),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary=f"Embedding population started as PID {launch.pid}.",
            detail=f"{launch.command.display}\n\nLog: {launch.log_path}",
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def cancel(self) -> WizardResult:
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary="Embedding population cancelled.",
        )

    def _steps(self) -> tuple[FormStep | ReviewStep, ...]:
        return (self._scope_step(), self._run_step(), self._review_step())

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
        if step.key == "scope":
            return {
                "intent": self._intent,
                "concept_scope": SCOPE_STANDARD
                if self._request.standard_only
                else SCOPE_ALL,
                "vocabulary_mode": self._request.vocabulary_mode,
                "vocabularies": "\n".join(self._selected_or_incomplete()),
            }
        return {
            "run_mode": "limit" if self._request.limit is not None else "complete",
            "num_embeddings": self._request.limit or DEFAULT_EMBEDDING_BACKFILL_LIMIT,
            "batch_size": self._request.batch_size,
        }

    def _submit_form(
        self,
        step: FormStep,
        values: Mapping[str, object],
    ) -> tuple[ValidationIssue, ...]:
        parsed: dict[str, object] = {}
        issues: list[ValidationIssue] = []
        for field in step.fields:
            try:
                parsed[field.key] = field.parse(values.get(field.key)).value
            except ValueError as exc:
                issues.append(ValidationIssue(str(exc), field_key=field.key))
        if issues:
            return tuple(issues)
        if step.key == "scope":
            return self._apply_scope(parsed)
        if step.key == "run":
            return self._apply_run(parsed)
        return ()

    def _apply_run(
        self,
        parsed: Mapping[str, object],
    ) -> tuple[ValidationIssue, ...]:
        run_mode = str(parsed["run_mode"])
        if run_mode not in {"complete", "limit"}:
            return (ValidationIssue("Choose a run size.", "run_mode"),)
        limit = int(str(parsed["num_embeddings"])) if run_mode == "limit" else None
        batch_size = int(str(parsed["batch_size"]))
        if limit is not None and limit <= 0:
            return (
                ValidationIssue("Backfill count must be positive.", "num_embeddings"),
            )
        if batch_size <= 0:
            return (ValidationIssue("Batch size must be positive.", "batch_size"),)
        self._request = EmbeddingPopulationRequest(
            standard_only=self._request.standard_only,
            vocabulary_mode=self._request.vocabulary_mode,
            vocabularies=self._request.vocabularies,
            limit=limit,
            batch_size=batch_size,
        )
        return ()

    def _known_vocabularies(self) -> tuple[str, ...]:
        if self._coverage is None or not self._coverage.coverage.available:
            return ()
        return tuple(row.vocabulary for row in self._coverage.coverage.rows)

    def _incomplete_vocabularies(self) -> tuple[str, ...]:
        if self._coverage is None or not self._coverage.coverage.available:
            return ()
        return self._coverage.incomplete_vocabularies

    def _selected_or_incomplete(self) -> tuple[str, ...]:
        """Prefill the list with what is actually missing, not with everything.

        Re-embedding a vocabulary that is already complete is the expensive
        mistake here, so the box opens on the vocabularies with concepts still
        pending and the operator narrows from there.
        """
        return self._request.vocabularies or self._incomplete_vocabularies()

    def _apply_scope(
        self,
        parsed: Mapping[str, object],
    ) -> tuple[ValidationIssue, ...]:
        intent = str(parsed["intent"])
        if intent not in {INTENT_POPULATE, INTENT_BACKFILL, INTENT_RECONCILE}:
            return (ValidationIssue("Choose the embedding intent.", "intent"),)
        self._intent = intent
        concept_scope = str(parsed["concept_scope"])
        if concept_scope not in {SCOPE_STANDARD, SCOPE_ALL}:
            return (ValidationIssue("Choose a concept scope.", "concept_scope"),)
        mode = str(parsed["vocabulary_mode"])
        if mode not in {
            VOCABULARY_MODE_ALL,
            VOCABULARY_MODE_INCOMPLETE,
            VOCABULARY_MODE_SELECTED,
        }:
            return (ValidationIssue("Choose which vocabularies to run.", "vocabulary_mode"),)

        if mode == VOCABULARY_MODE_ALL:
            vocabularies: tuple[str, ...] = ()
        elif mode == VOCABULARY_MODE_INCOMPLETE:
            vocabularies = self._incomplete_vocabularies()
            if not vocabularies:
                return (
                    ValidationIssue(
                        "No vocabulary has concepts pending under this scope.",
                        "vocabulary_mode",
                    ),
                )
        else:
            names = _parse_vocabularies(parsed.get("vocabularies"))
            if not names:
                return (
                    ValidationIssue(
                        "List at least one vocabulary, or choose a different mode.",
                        "vocabularies",
                    ),
                )
            known = self._known_vocabularies()
            unknown = tuple(name for name in names if name not in known)
            if unknown:
                return (
                    ValidationIssue(
                        "Not in the CDM under this scope: " + ", ".join(unknown),
                        "vocabularies",
                    ),
                )
            # Ordered by the coverage table, so the command reads the same way
            # the numbers on screen do regardless of typing order.
            vocabularies = tuple(name for name in known if name in set(names))

        self._request = EmbeddingPopulationRequest(
            standard_only=concept_scope == SCOPE_STANDARD,
            vocabulary_mode=mode,
            vocabularies=vocabularies,
            limit=self._request.limit,
            batch_size=self._request.batch_size,
        )
        return ()

    def _scope_step(self) -> FormStep:
        incomplete = self._incomplete_vocabularies()
        known = self._known_vocabularies()
        return FormStep(
            key="scope",
            title="Concept scope",
            purpose="Choose which concepts this run should embed.",
            fields=(
                FieldSpec(
                    "intent",
                    "Intent",
                    kind=FieldKind.CHOICE,
                    choices=(
                        ChoiceOption(INTENT_POPULATE, "Populate from scratch"),
                        ChoiceOption(INTENT_BACKFILL, "Backfill selected vocabularies"),
                        ChoiceOption(INTENT_RECONCILE, "Reconcile after vocabulary update"),
                    ),
                    required=False,
                    default=self._intent,
                    help="A numeric limit caps a run; it does not define its intent.",
                ),
                FieldSpec(
                    "concept_scope",
                    "Concepts",
                    kind=FieldKind.CHOICE,
                    choices=(
                        ChoiceOption(SCOPE_STANDARD, "Standard concepts only"),
                        ChoiceOption(SCOPE_ALL, "All concepts"),
                    ),
                    default=SCOPE_STANDARD
                    if self._request.standard_only
                    else SCOPE_ALL,
                    help=(
                        "Coverage above was measured for "
                        + (
                            "standard concepts only."
                            if self._coverage_standard_only()
                            else "all concepts."
                        )
                        + " Changing this changes what counts as missing."
                    ),
                ),
                FieldSpec(
                    "vocabulary_mode",
                    "Vocabularies",
                    kind=FieldKind.CHOICE,
                    choices=(
                        ChoiceOption(VOCABULARY_MODE_ALL, "Every vocabulary"),
                        ChoiceOption(
                            VOCABULARY_MODE_INCOMPLETE,
                            f"Only those with concepts pending ({len(incomplete)})",
                        ),
                        ChoiceOption(
                            VOCABULARY_MODE_SELECTED, "Only the ones I list below"
                        ),
                    ),
                    default=self._request.vocabulary_mode,
                ),
                FieldSpec(
                    "vocabularies",
                    "Selected vocabularies",
                    kind=FieldKind.MULTILINE,
                    required=False,
                    default="\n".join(self._selected_or_incomplete()),
                    help=(
                        "One per line, or comma separated. Used only by the "
                        "last mode above. "
                        + (
                            f"Available: {', '.join(known)}"
                            if known
                            else "Refresh coverage to list the available names."
                        )
                    ),
                ),
            ),
        )

    def _run_step(self) -> FormStep:
        return FormStep(
            key="run",
            title="Run size",
            purpose="Choose whether to run until no matching concepts are missing.",
            fields=(
                FieldSpec(
                    "run_mode",
                    "Run mode",
                    kind=FieldKind.CHOICE,
                    choices=(
                        ChoiceOption("complete", "Run to completion"),
                        ChoiceOption("limit", "Backfill next n"),
                    ),
                    default="complete",
                ),
                FieldSpec(
                    "num_embeddings",
                    "Backfill count",
                    kind=FieldKind.INTEGER,
                    required=False,
                    default=DEFAULT_EMBEDDING_BACKFILL_LIMIT,
                    minimum=1,
                ),
                FieldSpec(
                    "batch_size",
                    "Batch size",
                    kind=FieldKind.INTEGER,
                    default=DEFAULT_EMBEDDING_BATCH_SIZE,
                    minimum=1,
                ),
            ),
        )

    def _review_step(self) -> ReviewStep:
        command = self._command() if self._coverage is not None else None
        warnings = []
        if self._coverage is None:
            warnings.append("Embedding coverage is unavailable.")
        else:
            if self._request.standard_only != self._coverage_standard_only():
                # The counts on screen were produced by a different WHERE
                # clause, so they do not describe the run about to start.
                warnings.append(
                    "Coverage was measured for "
                    + (
                        "standard concepts only"
                        if self._coverage_standard_only()
                        else "all concepts"
                    )
                    + ", so the missing counts do not describe this run. "
                    "Refresh coverage after changing the concept scope."
                )
            if self._coverage.index.insert_warning is not None:
                warnings.append(self._coverage.index.insert_warning)
                warnings.extend(self._coverage.index.drop_sql)
        return ReviewStep(
            key="review",
            title="Review command",
            review=WizardReview(
                changes=(
                    ReviewChange("scope", None, _scope_label(self._request)),
                    ReviewChange("intent", None, self._intent),
                    ReviewChange(
                        "run size",
                        None,
                        "to completion"
                        if self._request.limit is None
                        else f"next {self._request.limit:,}",
                    ),
                    ReviewChange("batch size", None, self._request.batch_size),
                    ReviewChange("command", None, command.display if command else ""),
                ),
                effects=("Starts an omop-emb embeddings add-embeddings process.",),
                warnings=tuple(warnings),
                ready_to_apply=self._coverage is not None,
            ),
        )

    def _coverage_standard_only(self) -> bool:
        if self._coverage is None:
            return self._request.standard_only
        return self._coverage.coverage.scope.standard_only

    def _command(self) -> EmbeddingPopulationCommand:
        assert self._coverage is not None
        return build_embedding_population_command(
            self._coverage.configuration,
            self._request,
            config_path=self._session.configuration.path,
        )


def _parse_vocabularies(value: object) -> tuple[str, ...]:
    """Read a vocabulary list typed as lines, commas, or any mix of the two."""
    if value is None:
        return ()
    names = [
        part.strip()
        for chunk in str(value).replace(",", "\n").splitlines()
        for part in (chunk,)
        if part.strip()
    ]
    return tuple(dict.fromkeys(names))


def _scope_label(request: EmbeddingPopulationRequest) -> str:
    concept_scope = "standard concepts" if request.standard_only else "all concepts"
    if request.vocabulary_mode == "all":
        return f"{concept_scope}, all vocabularies"
    if request.vocabulary_mode == "incomplete":
        return f"{concept_scope}, all incomplete vocabularies"
    return f"{concept_scope}, {', '.join(request.vocabularies)}"
