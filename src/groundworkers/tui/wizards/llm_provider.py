from __future__ import annotations

from collections.abc import Mapping

from groundskeeping.contracts import (
    ChoiceOption,
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

from groundworkers.application.setup.llm_configuration import (
    DEFAULT_PROVIDER_URLS,
    SUPPORTED_LLM_PROVIDERS,
    apply_llm_configuration,
    plan_llm_configuration,
    scan_llm_models,
    update_draft,
)
from groundworkers.application.setup.models import DiagnosticSeverity
from groundworkers.tui.routes import SETUP_ROUTE
from groundworkers.tui.state import SetupSession


class LlmProviderConfigurationWizardController:
    """Guided LLM provider setup: endpoint first, model after inventory scan."""

    spec = WizardSpec(
        key="groundworkers.llm-provider-configuration",
        title="Configure LLM provider",
        purpose="Choose a provider endpoint, then select an available model.",
        apply_label="Save",
    )

    def __init__(self, session: SetupSession) -> None:
        self._session = session
        self._snapshot = session.configuration
        self._plan = plan_llm_configuration(self._snapshot)
        self._draft = self._plan.draft
        self._display_values = self._draft.safe_for_display()
        self._inventory: tuple[str, ...] = ()
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
            apply_llm_configuration(self._snapshot, self._draft)
        except PermissionError as exc:
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="LLM provider configuration is read-only.",
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
        except Exception as exc:  # noqa: BLE001 - rendered as safe setup failure
            return WizardResult(
                status=WizardResultStatus.FAILED,
                summary="LLM provider configuration was not saved.",
                detail=str(exc),
                refresh_pages=frozenset({SETUP_ROUTE.key}),
            )
        self._session.refresh_configuration()
        return WizardResult(
            status=WizardResultStatus.APPLIED,
            summary="LLM provider configuration saved.",
            refresh_pages=frozenset({SETUP_ROUTE.key}),
        )

    def cancel(self) -> WizardResult:
        return WizardResult(
            status=WizardResultStatus.CANCELLED,
            summary="LLM provider configuration cancelled.",
        )

    def _steps(self) -> tuple[FormStep | ReviewStep, ...]:
        return (
            self._endpoint_step(),
            self._model_step(),
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

    def _safe_values(self, step: FormStep | ReviewStep) -> Mapping[str, object]:
        if isinstance(step, ReviewStep):
            return {}
        values: dict[str, object] = {}
        for field in step.fields:
            values[field.key] = self._display_values.get(field.key, field.default)
        return values

    def _submit_form(
        self, step: FormStep, values: Mapping[str, object]
    ) -> tuple[ValidationIssue, ...]:
        if step.key == "endpoint" and not self._plan.editable:
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
        if step.key == "endpoint":
            provider = str(parsed["provider"])
            api_base = str(parsed["api_base"]).strip()
            if provider not in SUPPORTED_LLM_PROVIDERS:
                return (
                    ValidationIssue(
                        "Choose a supported provider.", field_key="provider"
                    ),
                )
            if not api_base:
                return (ValidationIssue("Enter a provider URL.", field_key="api_base"),)
            return self._apply_endpoint(provider=provider, api_base=api_base)
        if step.key == "model":
            model_name = str(parsed["default_model_name"]).strip()
            if model_name not in self._inventory:
                return (
                    ValidationIssue(
                        "Choose a model returned by the provider inventory.",
                        field_key="default_model_name",
                    ),
                )
            self._draft = update_draft(self._draft, default_model_name=model_name)
            self._display_values.update(self._draft.safe_for_display())
        return ()

    def _apply_endpoint(
        self, *, provider: str, api_base: str
    ) -> tuple[ValidationIssue, ...]:
        previous_model = self._draft.default_model_name
        self._draft = update_draft(
            self._draft,
            provider=provider,
            api_base=api_base,
            default_model_name=None,
        )
        result = scan_llm_models(self._draft)
        if result.inventory is None:
            return (
                ValidationIssue(
                    _scan_failure_message(result),
                    field_key="api_base",
                    status=SemanticStatus.ERROR,
                ),
            )
        if not result.inventory:
            return (
                ValidationIssue(
                    "The provider responded but returned no models.",
                    field_key="api_base",
                    status=SemanticStatus.WARNING,
                ),
            )
        self._inventory = result.inventory
        default_model = previous_model
        if default_model not in self._inventory:
            default_model = self._inventory[0]
        self._draft = update_draft(self._draft, default_model_name=default_model)
        self._display_values.update(self._draft.safe_for_display())
        return ()

    def _endpoint_step(self) -> FormStep:
        return FormStep(
            key="endpoint",
            title="Provider endpoint",
            purpose="Choose the provider type and endpoint to scan for available models.",
            fields=(
                FieldSpec(
                    "provider",
                    "Provider",
                    kind=FieldKind.CHOICE,
                    choices=tuple(
                        ChoiceOption(value=provider, label=_provider_label(provider))
                        for provider in SUPPORTED_LLM_PROVIDERS
                    ),
                    default=self._draft.provider,
                ),
                FieldSpec(
                    "api_base",
                    "Provider URL",
                    default=self._draft.api_base
                    or DEFAULT_PROVIDER_URLS.get(
                        self._draft.provider, "http://localhost:11434/v1"
                    ),
                ),
            ),
        )

    def _model_step(self) -> FormStep:
        return FormStep(
            key="model",
            title="Default model",
            purpose="Choose one model returned by the provider inventory.",
            fields=(
                FieldSpec(
                    "default_model_name",
                    "Model",
                    kind=FieldKind.CHOICE,
                    choices=tuple(
                        ChoiceOption(value=model, label=model)
                        for model in self._inventory
                    ),
                    default=self._draft.default_model_name,
                ),
            ),
        )

    def _review_step(self) -> ReviewStep:
        return ReviewStep(
            key="review",
            title="Review and apply",
            review=WizardReview(
                changes=(
                    ReviewChange(
                        "provider", self._plan.draft.provider, self._draft.provider
                    ),
                    ReviewChange(
                        "provider URL", self._plan.draft.api_base, self._draft.api_base
                    ),
                    ReviewChange(
                        "default model",
                        self._plan.draft.default_model_name,
                        self._draft.default_model_name,
                    ),
                ),
                effects=("Groundworkers will use this LLM provider after restart.",),
                warnings=(),
                ready_to_apply=self._plan.editable
                and self._draft.default_model_name is not None,
            ),
        )


def _provider_label(provider: str) -> str:
    if provider == "ollama":
        return "Ollama"
    return "OpenAI-compatible"


def _scan_failure_message(result) -> str:
    if result.failure is not None:
        return result.failure.detail
    for diagnostic in result.diagnostics:
        if diagnostic.severity is DiagnosticSeverity.ERROR:
            return diagnostic.message
    return "The provider inventory could not be loaded."
