from __future__ import annotations

from collections.abc import Callable, Mapping

from groundskeeping.contracts import (
    ChoiceOption,
    FieldKind,
    FieldSpec,
    FormStep,
    ReviewChange,
    ReviewStep,
    ValidationIssue,
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
    launch_embedding_population,
    load_embedding_coverage_report,
)
from groundworkers.application.setup.models import (
    EmbeddingCoverageReport,
    EmbeddingPopulationCommand,
    EmbeddingPopulationLaunch,
    EmbeddingPopulationRequest,
)
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession

VocabularyMode = str


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
        launcher: Callable[[EmbeddingPopulationCommand], EmbeddingPopulationLaunch]
        | None = None,
    ) -> None:
        self._session = session
        self._coverage = coverage or load_embedding_coverage_report(
            session.configuration,
            standard_only=session.embedding_standard_only,
        )
        self._launcher = launcher or launch_embedding_population
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
        return (self._run_step(), self._review_step())

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
        elif self._coverage.index.insert_warning is not None:
            warnings.append(self._coverage.index.insert_warning)
            warnings.extend(self._coverage.index.drop_sql)
        return ReviewStep(
            key="review",
            title="Review command",
            review=WizardReview(
                changes=(
                    ReviewChange("scope", None, _scope_label(self._request)),
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

    def _command(self) -> EmbeddingPopulationCommand:
        assert self._coverage is not None
        return build_embedding_population_command(
            self._coverage.configuration,
            self._request,
            config_path=self._session.configuration.path,
        )


def _scope_label(request: EmbeddingPopulationRequest) -> str:
    concept_scope = "standard concepts" if request.standard_only else "all concepts"
    if request.vocabulary_mode == "all":
        return f"{concept_scope}, all vocabularies"
    if request.vocabulary_mode == "incomplete":
        return f"{concept_scope}, all incomplete vocabularies"
    return f"{concept_scope}, {', '.join(request.vocabularies)}"
