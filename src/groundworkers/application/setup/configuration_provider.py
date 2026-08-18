"""Groundworkers' oa-configurator-backed mutation provider.

The generic wizard sees only portable fields, canonical-path diffs, effects, and
tagged outcomes. Real ``StackConfig`` candidates and submitted secrets remain in
private provider sessions addressed by opaque tokens.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from groundskeeping.configurator import (
    ConfigApplyIntent,
    ConfigApplyResult,
    ConfigApplyStatus,
    ConfigBranchCondition,
    ConfigDiff,
    ConfigDraft,
    ConfigPlan,
    ConfigStepResult,
    ConfigTarget,
    ConfigTargetKind,
    ConfigWorkflowSpec,
    ConfigWorkflowStep,
    EffectRef,
    MutationCapabilities,
    MutationOperation,
    MutationOperationUnsupported,
    UnavailableMutationService,
    build_config_diff,
)
from groundskeeping.contracts import (
    ChoiceOption,
    FieldKind,
    FieldSpec,
    ValidationIssue,
)
from oa_configurator import (  # type: ignore[import-untyped]
    ConfigurationError,
    StackConfig,
    plan_configure,
)
from pydantic import ValidationError

from groundworkers.application.setup.configuration import (
    ConfigurationConflictError,
    load_configuration,
    save_configuration,
)
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationState,
)
from groundworkers.config import GroundworkersConfig

CDM_SETUP_TARGET: Final = ConfigTarget(
    ConfigTargetKind.TOOL,
    "groundworkers.cdm",
    "Groundworkers CDM database",
)
MODEL_SETUP_TARGET: Final = ConfigTarget(
    ConfigTargetKind.TOOL,
    "groundworkers.embedding-model",
    "Groundworkers embedding model",
)
# Chat is a named `[models.*]` entry reached through `llm_model_name`, the same
# shape as the embedding model above and written through this same provider
# boundary. `embedding_model_name` is never repurposed as the chat model: the two
# journeys write independent entries and may point at different providers.
LLM_SETUP_TARGET: Final = ConfigTarget(
    ConfigTargetKind.TOOL,
    "groundworkers.llm",
    "Groundworkers chat model",
)

# Targets whose first step discovers live models from a provider endpoint.
_PROVIDER_DISCOVERY_STEPS: Final = {
    MODEL_SETUP_TARGET: ("provider", "provider_kind", "base_url", "api_key", "model_choice"),
    LLM_SETUP_TARGET: ("provider", "llm_provider_kind", "llm_base_url", "llm_api_key", "llm_model_choice"),
}

ModelDiscoverer = Callable[[str, str | None, str | None], Sequence[str]]
ApplyCallback = Callable[[], None]


@dataclass(repr=False)
class _MutationSession:
    target: ConfigTarget
    operation: MutationOperation
    expected_revision: str
    base: StackConfig
    fields: tuple[FieldSpec, ...]
    answers: dict[str, object] = field(default_factory=dict)
    changed_fields: frozenset[str] = frozenset()
    candidate: StackConfig | None = None
    apply_token: str | None = None


class GroundworkersConfigMutationService:
    """Adapt Groundworkers setup policy to Groundskeeping's mutation lifecycle."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        ownership: ConfigurationOwnership | None = None,
        model_discoverer: ModelDiscoverer | None = None,
        on_applied: ApplyCallback | None = None,
    ) -> None:
        self._path = Path(config_path).expanduser().resolve()
        self._ownership = ownership or ConfigurationOwnership()
        self._model_discoverer = model_discoverer or _no_model_discovery
        self._on_applied = on_applied
        self._sessions: dict[str, _MutationSession] = {}
        self._apply_tokens: dict[str, str] = {}

    def capabilities(
        self,
        target: ConfigTarget,
        operation: MutationOperation,
    ) -> MutationCapabilities:
        snapshot = self._load_snapshot()
        exists = _target_exists(snapshot.stack, target)
        supported = (
            operation is MutationOperation.UPDATE
            if exists
            else operation is MutationOperation.CREATE
        )
        reason = None
        if not self._ownership.editable:
            supported = False
            reason = self._ownership.guidance
        elif not supported:
            state = "already exists" if exists else "does not exist"
            reason = f"{target.title} {state}; choose the matching operation."
        return MutationCapabilities(target, operation, supported, reason)

    def begin(
        self,
        target: ConfigTarget,
        operation: MutationOperation,
    ) -> ConfigDraft:
        snapshot = self._load_snapshot()
        exists = _target_exists(snapshot.stack, target)
        supported = (
            operation is MutationOperation.UPDATE
            if exists
            else operation is MutationOperation.CREATE
        ) and self._ownership.editable
        if not supported:
            # Typed refusal, so a host can tell a legitimately unavailable operation
            # from a provider defect. `capabilities()` normally answers this first, but
            # that answer can go stale before this call.
            raise MutationOperationUnsupported(
                self._ownership.guidance
                if not self._ownership.editable
                else f"{operation.value.title()} is unavailable for {target.title}."
            )
        base = snapshot.stack.model_copy(deep=True) if snapshot.stack else StackConfig()
        if base.loaded_path is None:
            base.bind_loaded_path(self._path)
        token = secrets.token_urlsafe(24)
        fields = self._fields_for(target, base)
        revision = _required_revision(snapshot.revision)
        self._sessions[token] = _MutationSession(
            target=target,
            operation=operation,
            expected_revision=revision,
            base=base,
            fields=fields,
        )
        return ConfigDraft(
            target=target,
            operation=operation,
            session_token=token,
            expected_revision=revision,
        )

    def fields(self, draft: ConfigDraft) -> tuple[FieldSpec, ...]:
        return self._session(draft).fields

    def submit(
        self,
        draft: ConfigDraft,
        step_key: str,
        values: Mapping[str, object],
        *,
        discard_fields: frozenset[str] = frozenset(),
    ) -> ConfigStepResult:
        session = self._session(draft)
        declared = {item.key for item in session.fields}
        unknown = (set(values) | set(discard_fields)) - declared
        if unknown:
            raise ValueError(f"Unknown configuration fields: {sorted(unknown)}")
        if not step_key:
            raise ValueError("A configuration step key is required.")

        proposed = dict(session.answers)
        for key in discard_fields:
            proposed.pop(key, None)
        for key, value in values.items():
            if key in _SECRET_FIELDS and value in (None, ""):
                continue
            proposed[key] = value

        issues = self._submission_issues(session.target, step_key, proposed, values)
        if issues:
            return ConfigStepResult(
                issues=issues,
                changed_fields=session.changed_fields,
            )

        future_fields: tuple[FieldSpec, ...] = ()
        discovery = _PROVIDER_DISCOVERY_STEPS.get(session.target)
        if discovery is not None and step_key == discovery[0]:
            _, kind_key, base_url_key, api_key_key, choice_key = discovery
            try:
                models = tuple(
                    dict.fromkeys(
                        str(item).strip()
                        for item in self._model_discoverer(
                            str(proposed[kind_key]),
                            _optional_text(proposed.get(base_url_key)),
                            _optional_text(proposed.get(api_key_key)),
                        )
                        if str(item).strip()
                    )
                )
            except Exception:
                # Broad except: external discovery boundary.
                # The discoverer's own message is not forwarded: provider errors can
                # carry endpoints and credentials.
                return ConfigStepResult(
                    issues=(
                        ValidationIssue(
                            "Models could not be discovered from that provider endpoint.",
                            field_key=base_url_key,
                        ),
                    ),
                    changed_fields=session.changed_fields,
                )
            if not models:
                return ConfigStepResult(
                    issues=(
                        ValidationIssue(
                            "The provider endpoint returned no available models.",
                            field_key=base_url_key,
                        ),
                    ),
                    changed_fields=session.changed_fields,
                )
            future_fields = (
                FieldSpec(
                    key=choice_key,
                    label="Model",
                    kind=FieldKind.CHOICE,
                    choices=tuple(ChoiceOption(name, name) for name in models),
                    default=models[0],
                    help="Models reported by the accepted provider endpoint.",
                ),
            )

        session.answers = proposed
        session.changed_fields = frozenset(proposed)
        self._invalidate_plan(session)
        return ConfigStepResult(
            changed_fields=session.changed_fields,
            future_fields=future_fields,
        )

    def plan(self, draft: ConfigDraft) -> ConfigPlan:
        session = self._session(draft)
        self._invalidate_plan(session)
        try:
            candidate = self._candidate(session)
        except (ConfigurationError, ValidationError, ValueError, TypeError):
            return ConfigPlan(
                target=session.target,
                operation=session.operation,
                diff=ConfigDiff(session.target, ()),
                issues=(
                    ValidationIssue(
                        "The proposed configuration is incomplete or invalid."
                    ),
                ),
                expected_revision=session.expected_revision,
            )

        # Both sides must come from the same projection or the diff is misleading:
        # `plan_configure` returns a validated candidate with every package default
        # materialised, while a hand-written base carries only the keys an operator
        # actually wrote. Comparing them directly reported each unset default as a
        # change (`mcp.port: None -> 8000`), burying the fields the journey touched.
        original = _flatten_stack(_normalise_for_diff(session.base))
        planned = _flatten_stack(_normalise_for_diff(candidate))
        diff = build_config_diff(
            session.target,
            original,
            planned,
            sensitive_fields=frozenset(
                path
                for path in set(original) | set(planned)
                if _is_sensitive_path(path)
            ),
        )
        token = secrets.token_urlsafe(24)
        session.candidate = candidate
        session.apply_token = token
        self._apply_tokens[token] = draft.session_token
        return ConfigPlan(
            target=session.target,
            operation=session.operation,
            diff=diff,
            effects=_effects_for(session),
            warnings=(
                "This plan updates several named stack entries as one Groundworkers setup goal.",
            ),
            apply_token=token,
            expected_revision=session.expected_revision,
        )

    def apply(self, intent: ConfigApplyIntent) -> ConfigApplyResult:
        session_token = self._apply_tokens.pop(intent.apply_token, None)
        if session_token is None:
            return ConfigApplyResult(
                ConfigApplyStatus.REJECTED,
                "This configuration plan is no longer available.",
                "Review the current configuration and prepare a new plan.",
            )
        session = self._sessions.get(session_token)
        if session is None:
            return ConfigApplyResult(
                ConfigApplyStatus.REJECTED,
                "This configuration session has closed.",
            )
        session.apply_token = None
        if (
            intent.target != session.target
            or intent.operation is not session.operation
            or intent.expected_revision != session.expected_revision
            or session.candidate is None
        ):
            return ConfigApplyResult(
                ConfigApplyStatus.REJECTED,
                "The apply request does not match its prepared configuration plan.",
            )
        try:
            save_configuration(
                session.candidate,
                path=self._path,
                expected_revision=session.expected_revision,
                ownership=self._ownership,
            )
        except ConfigurationConflictError:
            return ConfigApplyResult(
                ConfigApplyStatus.CONFLICTED,
                "The configuration changed before this plan could be applied.",
                "Reload the configuration, review the new state, and try again.",
            )
        except PermissionError:
            # The write was attempted and the filesystem refused it. That is FAILED, not
            # REJECTED: the request was acceptable, the operation errored.
            return ConfigApplyResult(
                ConfigApplyStatus.FAILED,
                "The configuration could not be saved.",
                "Grant write access to the configuration file and try again. "
                "The previous configuration remains authoritative.",
            )
        except (ConfigurationError, ValidationError):
            # The candidate itself was not acceptable, so nothing was written.
            return ConfigApplyResult(
                ConfigApplyStatus.REJECTED,
                "The configuration change was rejected.",
                "Review the current configuration and prepare a new plan.",
            )
        except Exception:
            # Broad except: translated to a secret-safe provider result.
            return ConfigApplyResult(
                ConfigApplyStatus.FAILED,
                "The configuration could not be saved.",
                "The previous configuration remains authoritative.",
            )

        self._sessions.pop(session_token, None)
        if self._on_applied is not None:
            self._on_applied()
        refresh = (
            frozenset({"configuration", "database", "setup"})
            if session.target == CDM_SETUP_TARGET
            else frozenset({"configuration", "models"})
        )
        return ConfigApplyResult(
            ConfigApplyStatus.APPLIED,
            "Groundworkers configuration was updated.",
            refresh_pages=refresh,
        )

    def cancel(self, draft: ConfigDraft) -> None:
        session = self._sessions.pop(draft.session_token, None)
        if session is None:
            return
        if session.apply_token is not None:
            self._apply_tokens.pop(session.apply_token, None)
        session.answers.clear()
        session.candidate = None
        session.apply_token = None

    def _load_snapshot(self):
        snapshot = load_configuration(
            config_path=self._path,
            ownership=self._ownership,
        )
        if snapshot.state is ConfigurationState.MALFORMED:
            raise UnavailableMutationService(
                "The configuration file must be repaired before it can be edited."
            )
        return snapshot

    def _session(self, draft: ConfigDraft) -> _MutationSession:
        session = self._sessions.get(draft.session_token)
        if session is None:
            raise ValueError("The configuration session is unavailable.")
        if (
            draft.target != session.target
            or draft.operation is not session.operation
            or draft.expected_revision != session.expected_revision
        ):
            raise ValueError("The draft does not match its provider session.")
        return session

    def _fields_for(
        self,
        target: ConfigTarget,
        stack: StackConfig,
    ) -> tuple[FieldSpec, ...]:
        if target == CDM_SETUP_TARGET:
            return _cdm_fields(stack)
        if target == MODEL_SETUP_TARGET:
            return _model_fields(stack)
        if target == LLM_SETUP_TARGET:
            return _llm_fields(stack)
        raise ValueError("Groundworkers does not support this configuration target.")

    def _submission_issues(
        self,
        target: ConfigTarget,
        step_key: str,
        proposed: Mapping[str, object],
        submitted: Mapping[str, object],
    ) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if target == CDM_SETUP_TARGET:
            for key in ("connection_name", "database_name", "cdm_db_name"):
                if key in submitted and not _optional_text(submitted.get(key)):
                    issues.append(ValidationIssue("A name is required.", field_key=key))
            dialect = _optional_text(proposed.get("dialect"))
            if dialect not in {"sqlite", "postgresql+psycopg"}:
                issues.append(
                    ValidationIssue(
                        "Choose a supported database type.", field_key="dialect"
                    )
                )
            if (
                step_key == "server"
                and dialect != "sqlite"
                and not _optional_text(proposed.get("host"))
            ):
                issues.append(
                    ValidationIssue("A database host is required.", field_key="host")
                )
        elif target == MODEL_SETUP_TARGET:
            if step_key == "provider" and not _optional_text(
                proposed.get("provider_name")
            ):
                issues.append(
                    ValidationIssue(
                        "A provider name is required.", field_key="provider_name"
                    )
                )
            if step_key == "model":
                for key in ("model_entry_name", "model_choice"):
                    if not _optional_text(proposed.get(key)):
                        issues.append(
                            ValidationIssue("A model is required.", field_key=key)
                        )
        elif target == LLM_SETUP_TARGET:
            if step_key == "provider":
                if not _optional_text(proposed.get("llm_provider_name")):
                    issues.append(
                        ValidationIssue(
                            "A provider name is required.", field_key="llm_provider_name"
                        )
                    )
                if _optional_text(proposed.get("llm_provider_kind")) not in _LLM_PROVIDERS:
                    issues.append(
                        ValidationIssue(
                            "Choose a supported provider.", field_key="llm_provider_kind"
                        )
                    )
                if not _optional_text(proposed.get("llm_base_url")):
                    issues.append(
                        ValidationIssue(
                            "A provider endpoint is required.", field_key="llm_base_url"
                        )
                    )
            if step_key == "model":
                for key in ("llm_model_entry_name", "llm_model_choice"):
                    if not _optional_text(proposed.get(key)):
                        issues.append(
                            ValidationIssue("A chat model is required.", field_key=key)
                        )
        return tuple(issues)

    def _candidate(self, session: _MutationSession) -> StackConfig:
        if session.target == CDM_SETUP_TARGET:
            set_dict = _cdm_set_dict(session.answers)
        elif session.target == MODEL_SETUP_TARGET:
            set_dict = _model_set_dict(session.answers)
        elif session.target == LLM_SETUP_TARGET:
            set_dict = _llm_set_dict(session.answers)
        else:
            raise ValueError("Unsupported Groundworkers configuration target.")
        candidate = plan_configure(GroundworkersConfig, session.base, set_dict)
        GroundworkersConfig.validate_candidate(candidate)
        return candidate

    def _invalidate_plan(self, session: _MutationSession) -> None:
        if session.apply_token is not None:
            self._apply_tokens.pop(session.apply_token, None)
        session.apply_token = None
        session.candidate = None


def cdm_setup_workflow(
    operation: MutationOperation,
) -> ConfigWorkflowSpec:
    """Describe the reusable Groundworkers CDM setup journey."""

    return ConfigWorkflowSpec(
        key="groundworkers-cdm",
        target=CDM_SETUP_TARGET,
        operation=operation,
        title="Configure the Groundworkers CDM database",
        purpose="Connect Groundworkers to the CDM and vocabulary schemas it should use.",
        steps=(
            ConfigWorkflowStep(
                "connection",
                "Choose the connection",
                ("connection_name", "dialect"),
            ),
            ConfigWorkflowStep(
                "server",
                "Enter server details",
                ("host", "port", "user", "password"),
                when=(
                    ConfigBranchCondition(
                        "dialect",
                        frozenset({"sqlite"}),
                        negated=True,
                    ),
                ),
            ),
            ConfigWorkflowStep(
                "database",
                "Choose the database",
                ("database_name",),
            ),
            ConfigWorkflowStep(
                "cdm",
                "Describe the CDM",
                ("cdm_db_name", "schema_name", "vocab_schema", "results_schema"),
            ),
        ),
    )


def model_setup_workflow(
    operation: MutationOperation,
) -> ConfigWorkflowSpec:
    """Describe provider discovery followed by a refreshed model choice."""

    return ConfigWorkflowSpec(
        key="groundworkers-embedding-model",
        target=MODEL_SETUP_TARGET,
        operation=operation,
        title="Configure the Groundworkers embedding model",
        purpose="Connect to a provider, discover its models, and choose one for embeddings.",
        steps=(
            ConfigWorkflowStep(
                "provider",
                "Connect to the provider",
                ("provider_name", "provider_kind", "base_url", "api_key"),
            ),
            ConfigWorkflowStep(
                "model",
                "Choose the model",
                ("model_entry_name", "model_choice"),
            ),
        ),
    )


def llm_setup_workflow(
    operation: MutationOperation,
) -> ConfigWorkflowSpec:
    """Describe chat provider discovery followed by a refreshed model choice."""

    return ConfigWorkflowSpec(
        key="groundworkers-llm",
        target=LLM_SETUP_TARGET,
        operation=operation,
        title="Configure the Groundworkers chat model",
        purpose="Connect to a provider, discover its models, and choose one for chat.",
        steps=(
            ConfigWorkflowStep(
                "provider",
                "Connect to the provider",
                ("llm_provider_name", "llm_provider_kind", "llm_base_url", "llm_api_key"),
            ),
            ConfigWorkflowStep(
                "model",
                "Choose the chat model",
                ("llm_model_entry_name", "llm_model_choice"),
            ),
        ),
    )


def _cdm_fields(stack: StackConfig) -> tuple[FieldSpec, ...]:
    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    cdm_name = str(tool.get("cdm_db", "cdm_db"))
    database = stack.databases.get(cdm_name)
    connection_name = getattr(database, "connection", "cdm_main")
    connection = stack.connections.get(connection_name)
    dialect = getattr(connection, "dialect", "sqlite")
    return (
        FieldSpec("connection_name", "Connection name", default=connection_name),
        FieldSpec(
            "dialect",
            "Database type",
            kind=FieldKind.CHOICE,
            default=dialect,
            choices=(
                ChoiceOption("sqlite", "SQLite"),
                ChoiceOption("postgresql+psycopg", "PostgreSQL"),
            ),
        ),
        FieldSpec(
            "host", "Host", required=False, default=getattr(connection, "host", None)
        ),
        FieldSpec(
            "port",
            "Port",
            kind=FieldKind.INTEGER,
            required=False,
            minimum=1,
            maximum=65535,
            default=getattr(connection, "port", None),
        ),
        FieldSpec(
            "user", "User", required=False, default=getattr(connection, "user", None)
        ),
        FieldSpec(
            "password",
            "Password",
            kind=FieldKind.SECRET,
            required=False,
            sensitive=True,
            help="Leave blank to preserve an existing password.",
        ),
        FieldSpec(
            "database_name",
            "Database name or SQLite path",
            default=getattr(connection, "database_name", ":memory:"),
        ),
        FieldSpec("cdm_db_name", "CDM database entry", default=cdm_name),
        FieldSpec(
            "schema_name",
            "CDM schema",
            default=getattr(database, "schema_name", "main"),
        ),
        FieldSpec(
            "vocab_schema",
            "Vocabulary schema",
            required=False,
            default=getattr(database, "vocab_schema", None),
        ),
        FieldSpec(
            "results_schema",
            "Results schema",
            required=False,
            default=getattr(database, "results_schema", None),
        ),
    )


def _model_fields(stack: StackConfig) -> tuple[FieldSpec, ...]:
    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    model_name = _optional_text(tool.get("embedding_model_name"))
    model = stack.models.get(model_name) if model_name else None
    provider_name = getattr(model, "provider", "embedding_provider")
    provider = stack.providers.get(provider_name)
    existing_model = getattr(model, "model", None)
    pending = existing_model or "pending"
    return (
        FieldSpec("provider_name", "Provider entry", default=provider_name),
        FieldSpec(
            "provider_kind",
            "Provider",
            kind=FieldKind.CHOICE,
            default=getattr(provider, "provider", "ollama"),
            choices=(
                ChoiceOption("ollama", "Ollama"),
                ChoiceOption("openai", "OpenAI-compatible"),
            ),
        ),
        FieldSpec(
            "base_url",
            "Provider endpoint",
            required=False,
            default=getattr(provider, "base_url", None),
        ),
        FieldSpec(
            "api_key",
            "API key",
            kind=FieldKind.SECRET,
            required=False,
            sensitive=True,
            help="Leave blank to preserve an existing key.",
        ),
        FieldSpec(
            "model_entry_name", "Model entry", default=model_name or "embedding_model"
        ),
        FieldSpec(
            "model_choice",
            "Model",
            kind=FieldKind.CHOICE,
            choices=(ChoiceOption(pending, pending),),
            default=existing_model,
            disabled=existing_model is None,
            help="Complete the provider step to discover available models.",
        ),
    )


def _llm_fields(stack: StackConfig) -> tuple[FieldSpec, ...]:
    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    model_name = _optional_text(tool.get("llm_model_name"))
    model = stack.models.get(model_name) if model_name else None
    provider_name = getattr(model, "provider", "chat_provider")
    provider = stack.providers.get(provider_name)
    existing_model = getattr(model, "model", None)
    pending = existing_model or "pending"
    return (
        FieldSpec("llm_provider_name", "Provider entry", default=provider_name),
        FieldSpec(
            "llm_provider_kind",
            "Provider",
            kind=FieldKind.CHOICE,
            default=getattr(provider, "provider", "ollama"),
            choices=tuple(
                ChoiceOption(key, label) for key, label in _LLM_PROVIDERS.items()
            ),
        ),
        FieldSpec(
            "llm_base_url",
            "Provider endpoint",
            default=getattr(provider, "base_url", None) or _DEFAULT_LLM_ENDPOINT,
        ),
        FieldSpec(
            "llm_api_key",
            "API key",
            kind=FieldKind.SECRET,
            required=False,
            sensitive=True,
            help="Leave blank to preserve an existing key.",
        ),
        FieldSpec(
            "llm_model_entry_name", "Model entry", default=model_name or "chat_model"
        ),
        FieldSpec(
            "llm_model_choice",
            "Chat model",
            kind=FieldKind.CHOICE,
            choices=(ChoiceOption(pending, pending),),
            default=existing_model,
            disabled=existing_model is None,
            help="Complete the provider step to discover available models.",
        ),
    )


def _cdm_set_dict(answers: Mapping[str, object]) -> dict[str, object]:
    connection: dict[str, object] = {
        "name": str(answers["connection_name"]),
        "dialect": str(answers["dialect"]),
        "database_name": str(answers["database_name"]),
    }
    for key in ("host", "port", "user", "password"):
        value = answers.get(key)
        if value not in (None, ""):
            connection[key] = value
    database: dict[str, object] = {
        "name": str(answers["cdm_db_name"]),
        "connection": connection,
        "schema_name": str(answers["schema_name"]),
    }
    for key in ("vocab_schema", "results_schema"):
        value = answers.get(key)
        if value not in (None, ""):
            database[key] = value
    return {"cdm_db": database}


def _model_set_dict(answers: Mapping[str, object]) -> dict[str, object]:
    provider: dict[str, object] = {
        "name": str(answers["provider_name"]),
        "provider": str(answers["provider_kind"]),
    }
    for key in ("base_url", "api_key"):
        value = answers.get(key)
        if value not in (None, ""):
            provider[key] = value
    return {
        "embedding_model_name": {
            "name": str(answers["model_entry_name"]),
            "provider": provider,
            "model": str(answers["model_choice"]),
            "embeddings": True,
        }
    }


def _llm_set_dict(answers: Mapping[str, object]) -> dict[str, object]:
    """Build the chat provider and model entries.

    Structurally identical to :func:`_model_set_dict`; the two differ only in
    which reference field they populate and in declaring `structured_output`
    rather than `embeddings`. A blank API key answer means "keep the existing
    one" (the submit path already drops blank secrets), so it is simply omitted
    and the stored provider value survives.
    """
    provider: dict[str, object] = {
        "name": str(answers["llm_provider_name"]),
        "provider": str(answers["llm_provider_kind"]),
    }
    for answer_key, provider_key in (("llm_base_url", "base_url"), ("llm_api_key", "api_key")):
        value = answers.get(answer_key)
        if value not in (None, ""):
            provider[provider_key] = value
    return {
        "llm_model_name": {
            "name": str(answers["llm_model_entry_name"]),
            "provider": provider,
            "model": str(answers["llm_model_choice"]),
            # The structured tool path asks for JSON mode, so the entry declares
            # it. omop-llm treats every capability as opt-in.
            "structured_output": True,
        }
    }


def _target_exists(stack: StackConfig | None, target: ConfigTarget) -> bool:
    if stack is None:
        return False
    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    if target == CDM_SETUP_TARGET:
        name = tool.get("cdm_db")
        return isinstance(name, str) and name in stack.databases
    if target == MODEL_SETUP_TARGET:
        name = tool.get("embedding_model_name")
        return isinstance(name, str) and name in stack.models
    if target == LLM_SETUP_TARGET:
        name = tool.get("llm_model_name")
        return isinstance(name, str) and name in stack.models
    raise ValueError("Groundworkers does not support this configuration target.")


def _normalise_for_diff(stack: StackConfig) -> StackConfig:
    """Materialise Groundworkers' package defaults so two stacks are comparable.

    Only the Groundworkers tool mapping is normalised; other packages' sections are
    left exactly as written, because this provider does not own their schemas and
    must not invent values for them.
    """
    tool = stack.tools.get(GroundworkersConfig.tool_name)
    if tool is None:
        return stack
    try:
        materialised = GroundworkersConfig.model_validate(tool).model_dump(
            mode="python"
        )
    except ValidationError:
        # An invalid current section still has to be shown as-is rather than hidden.
        return stack
    normalised = stack.model_copy(deep=True)
    normalised.tools[GroundworkersConfig.tool_name] = materialised
    return normalised


def _flatten_stack(stack: StackConfig) -> dict[str, object]:
    flattened: dict[str, object] = {}

    def visit(prefix: str, value: object) -> None:
        if isinstance(value, Mapping):
            for key in sorted(value, key=str):
                path = f"{prefix}.{key}" if prefix else str(key)
                visit(path, value[key])
            return
        if isinstance(value, list | tuple):
            for index, item in enumerate(value):
                visit(f"{prefix}.{index}", item)
            return
        flattened[prefix] = value

    visit("", stack.model_dump(mode="python", exclude_none=True))
    return flattened


def _is_sensitive_path(path: str) -> bool:
    key = path.rsplit(".", 1)[-1].lower()
    return any(marker in key for marker in ("password", "api_key", "secret", "token"))


def _effects_for(session: _MutationSession) -> tuple[EffectRef, ...]:
    if session.target == CDM_SETUP_TARGET:
        connection_name = str(session.answers.get("connection_name", "cdm_main"))
        database_name = str(session.answers.get("cdm_db_name", "cdm_db"))
        return (
            EffectRef(
                session.operation.value,
                session.target,
                "physical database connection",
                ConfigTarget(
                    ConfigTargetKind.CONNECTION, connection_name, connection_name
                ),
                "connections",
            ),
            EffectRef(
                session.operation.value,
                session.target,
                "logical CDM and vocabulary database",
                ConfigTarget(ConfigTargetKind.DATABASE, database_name, database_name),
                "databases",
            ),
        )
    if session.target == LLM_SETUP_TARGET:
        provider_name = str(session.answers.get("llm_provider_name", "chat_provider"))
        model_name = str(session.answers.get("llm_model_entry_name", "chat_model"))
        model_label = "chat model"
    else:
        provider_name = str(session.answers.get("provider_name", "embedding_provider"))
        model_name = str(session.answers.get("model_entry_name", "embedding_model"))
        model_label = "embedding model"
    return (
        EffectRef(
            session.operation.value,
            session.target,
            "model provider",
            ConfigTarget(ConfigTargetKind.PROVIDER, provider_name, provider_name),
            "providers",
        ),
        EffectRef(
            session.operation.value,
            session.target,
            model_label,
            ConfigTarget(ConfigTargetKind.MODEL, model_name, model_name),
            "models",
        ),
    )


def _required_revision(value: str | None) -> str:
    if value is None:
        raise UnavailableMutationService(
            "The configuration revision could not be determined safely."
        )
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _no_model_discovery(
    provider: str,
    base_url: str | None,
    api_key: str | None,
) -> Sequence[str]:
    del provider, base_url, api_key
    raise RuntimeError("No model discovery service is configured.")


_SECRET_FIELDS = frozenset({"password", "api_key", "llm_api_key"})

_LLM_PROVIDERS: Final = {
    "ollama": "Ollama",
    "openai-compatible": "OpenAI-compatible",
}
_DEFAULT_LLM_ENDPOINT: Final = "http://localhost:11434/v1"
