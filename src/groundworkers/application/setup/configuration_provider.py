"""Groundworkers' oa-configurator-backed mutation provider.

The generic wizard sees only portable fields, canonical-path diffs, effects, and
tagged outcomes. Real ``StackConfig`` candidates and submitted secrets remain in
private provider sessions addressed by opaque tokens.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Final

from groundskeeping.configurator.controller import (
    ConfigBranchCondition,
    ConfigWorkflowSpec,
    ConfigWorkflowStep,
)
from groundskeeping.configurator.models import ConfigTarget, ConfigTargetKind
from groundskeeping.configurator.mutation import (
    ConfigApplyIntent,
    ConfigApplyResult,
    ConfigApplyStatus,
    ConfigDiff,
    ConfigDraft,
    ConfigPlan,
    ConfigStepResult,
    EffectRef,
    MutationCapabilities,
    MutationOperation,
    MutationOperationUnsupported,
    UnavailableMutationService,
    build_config_diff,
)
from groundskeeping.contracts.actions import (
    ChoiceOption,
    FieldKind,
    FieldSpec,
    ValidationIssue,
)
from oa_configurator import (  # type: ignore[import-untyped]
    CDMDatabaseConfig,
    ConfigurationError,
    PackageConfigBase,
    StackConfig,
    is_sensitive,
    plan_configure,
)
from omop_llm import canonical_model_name, supported_providers
from pydantic import BaseModel, ValidationError

from groundworkers.application.setup.configuration import (
    ConfigurationConflictError,
    load_configuration,
    save_configuration,
)
from groundworkers.application.setup.embedding_registry import (
    RegistryLister,
    list_registered_models,
)
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationState,
    EmbeddingStoreSnapshot,
    EmbeddingStoreState,
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
# Setup sections write independent entries and may point at different providers.
VECTOR_STORE_SETUP_TARGET: Final = ConfigTarget(
    ConfigTargetKind.TOOL,
    "groundworkers.vector-store",
    "Groundworkers embedding store",
)
_OMOP_EMB_TOOL_NAME: Final = "omop_emb"
_CDM_CLI_TOOL_NAMES: Final = ("omop_alchemy", "omop_graph")
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

# Whether the chosen model comes from the embedding store's registry (vectors
# already exist) or from the provider (population must follow).
MODEL_SOURCE_REGISTERED: Final = "registered"
MODEL_SOURCE_NEW: Final = "new"

# Where the vectors are stored. `cdm` derives the store's database from the CDM
# entry -- same connection, same schema -- so the ordinary case needs no answers
# at all; `separate` opens the step that asks for them. oa-configurator requires
# a vector store to name a *generic* database entry and a CDM entry is a distinct
# kind, so `cdm` still writes an entry; it just derives every field of it.
STORE_LOCATION_CDM: Final = "cdm"
STORE_LOCATION_SEPARATE: Final = "separate"
# Suffix for the derived entry. The name is a config-level label only: the
# connection and schema underneath it are the CDM's own.
CDM_VECTOR_DATABASE_SUFFIX: Final = "_vectors"

# Which omop-emb backend can actually store vectors in a given CDM dialect.
_BACKEND_FOR_DIALECT: Final = {
    "sqlite": "sqlitevec",
    "postgresql": "pgvector",
}
_BACKEND_CHOICES: Final = (
    ChoiceOption("sqlitevec", "sqlite-vec (single file, no server)"),
    ChoiceOption("pgvector", "pgvector (PostgreSQL extension)"),
)
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
    # Carried between steps: the provider's live inventory, and what the
    # embedding store already holds vectors for.
    provider_models: tuple[str, ...] = ()
    registered_models: tuple[str, ...] = ()


class GroundworkersConfigMutationService:
    """Adapt Groundworkers setup policy to Groundskeeping's mutation lifecycle."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        ownership: ConfigurationOwnership | None = None,
        model_discoverer: ModelDiscoverer | None = None,
        registry_lister: RegistryLister | None = None,
        on_applied: ApplyCallback | None = None,
    ) -> None:
        self._path = Path(config_path).expanduser().resolve()
        self._ownership = ownership or ConfigurationOwnership()
        self._model_discoverer = model_discoverer or _no_model_discovery
        self._registry_lister = registry_lister or list_registered_models
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

        # Derived from the field specs rather than a second hardcoded list: a
        # blank answer for a secret means "keep the stored one", and the field
        # already declares that it is a secret.
        secret_fields = {item.key for item in session.fields if item.sensitive}

        proposed = dict(session.answers)
        for key in discard_fields:
            proposed.pop(key, None)
        for key, value in values.items():
            if key in secret_fields and value in (None, ""):
                continue
            proposed[key] = value

        issues = self._submission_issues(session, step_key, proposed, values)
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
            session.provider_models = models
            if session.target is MODEL_SETUP_TARGET or session.target == MODEL_SETUP_TARGET:
                # The registry step comes next for embeddings, so refresh its
                # choice with what the store actually holds.
                future_fields = (self._model_source_field(session),)
            else:
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
        elif session.target == VECTOR_STORE_SETUP_TARGET and step_key == "location":
            # The CDM's dialect decides which omop-emb backend can store vectors
            # in it, so once the location is known the choice narrows to one.
            future_fields = (_backend_type_field(session.base, proposed),)
        elif session.target == MODEL_SETUP_TARGET and step_key == "registry":
            future_fields = (self._model_choice_field(session, proposed),)
        elif session.target == MODEL_SETUP_TARGET and step_key == "model":
            # The convention a model is trained with can only be preselected
            # once there is a model to look at.
            future_fields = (
                _prefix_convention_field(
                    _optional_text(proposed.get("model_choice")),
                    default=None,
                ),
            )

        session.answers = proposed
        session.changed_fields = frozenset(proposed)
        self._invalidate_plan(session)
        return ConfigStepResult(
            changed_fields=session.changed_fields,
            future_fields=future_fields,
        )

    def _model_source_field(self, session: _MutationSession) -> FieldSpec:
        """Offer the registry only when it actually holds something."""
        store = self._registry_lister(self._load_snapshot())
        session.registered_models = tuple(
            model.model_name for model in store.models
        )
        registered = session.registered_models
        if not registered:
            return FieldSpec(
                "model_source",
                "Model source",
                kind=FieldKind.CHOICE,
                default=MODEL_SOURCE_NEW,
                choices=(
                    ChoiceOption(
                        MODEL_SOURCE_NEW, "Register a new model from the provider"
                    ),
                ),
                disabled=True,
                help=_registry_summary(store),
            )
        return FieldSpec(
            "model_source",
            "Model source",
            kind=FieldKind.CHOICE,
            default=MODEL_SOURCE_REGISTERED,
            choices=(
                ChoiceOption(
                    MODEL_SOURCE_REGISTERED,
                    f"Use one of the {len(registered)} registered model(s)",
                ),
                ChoiceOption(
                    MODEL_SOURCE_NEW, "Register a new model from the provider"
                ),
            ),
            help=_registry_summary(store),
        )

    def _model_choice_field(
        self, session: _MutationSession, proposed: Mapping[str, object]
    ) -> FieldSpec:
        use_registered = (
            _optional_text(proposed.get("model_source")) == MODEL_SOURCE_REGISTERED
            and session.registered_models
        )
        names = (
            session.registered_models if use_registered else session.provider_models
        )
        return FieldSpec(
            key="model_choice",
            label="Model",
            kind=FieldKind.CHOICE,
            choices=tuple(ChoiceOption(name, name) for name in names),
            default=names[0] if names else None,
            help=(
                "Registered in the embedding store, so vectors already exist."
                if use_registered
                else "Reported by the provider. Populate embeddings after saving."
            ),
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
        # materialised, while a hand-written base carries only explicitly written
        # keys. Normalising both sides prevents unset defaults from appearing as
        # changes and keeps the review focused on the submitted fields.
        original = _flatten_stack(_normalise_for_diff(session.base))
        planned = _flatten_stack(_normalise_for_diff(candidate))
        diff = build_config_diff(
            session.target,
            original,
            planned,
            sensitive_fields=frozenset(
                _sensitive_paths(session.base) | _sensitive_paths(candidate)
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
        if target == VECTOR_STORE_SETUP_TARGET:
            return _vector_store_fields(stack)
        if target == LLM_SETUP_TARGET:
            return _llm_fields(stack)
        raise ValueError("Groundworkers does not support this configuration target.")

    def _submission_issues(
        self,
        session: _MutationSession,
        step_key: str,
        proposed: Mapping[str, object],
        submitted: Mapping[str, object],
    ) -> tuple[ValidationIssue, ...]:
        """Validate one step's answers against the workflow and the stack.

        Takes the session rather than the target alone: some answers are only
        wrong in the context of what the stack already holds, e.g. a backend the
        CDM database's dialect cannot serve.
        """
        target = session.target
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
            if step_key == "database" and dialect == "sqlite":
                sqlite_path = _optional_text(proposed.get("database_name"))
                if sqlite_path == ":memory:":
                    issues.append(
                        ValidationIssue(
                            "An in-memory SQLite database cannot be used as an OMOP CDM.",
                            field_key="database_name",
                        )
                    )
                elif sqlite_path and not _existing_sqlite_path(
                    sqlite_path,
                    stack=session.base,
                ):
                    issues.append(
                        ValidationIssue(
                            "Choose an existing SQLite CDM file.",
                            field_key="database_name",
                        )
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
                issues.extend(
                    _model_name_issues(
                        proposed,
                        kind_key="provider_kind",
                        model_key="model_choice",
                    )
                )
            if step_key == "custom_prefixes" and not (
                _prefix_text(proposed.get("document_prefix"))
                or _prefix_text(proposed.get("query_prefix"))
            ):
                issues.append(
                    ValidationIssue(
                        "Enter at least one prefix, or go back and choose "
                        "'No prefixes'.",
                        field_key="document_prefix",
                    )
                )
        elif target == VECTOR_STORE_SETUP_TARGET:
            if step_key == "location":
                location = _optional_text(proposed.get("store_location"))
                if location not in {STORE_LOCATION_CDM, STORE_LOCATION_SEPARATE}:
                    issues.append(
                        ValidationIssue(
                            "Choose where the vectors are stored.",
                            field_key="store_location",
                        )
                    )
                elif (
                    location == STORE_LOCATION_CDM
                    and _cdm_entry(session.base)[1] is None
                ):
                    issues.append(
                        ValidationIssue(
                            "There is no CDM database to store vectors in yet. "
                            "Configure the CDM database first, or choose somewhere else.",
                            field_key="store_location",
                        )
                    )
            if step_key == "store":
                for key in ("store_entry_name", "backend_type"):
                    if not _optional_text(proposed.get(key)):
                        issues.append(
                            ValidationIssue("A value is required.", field_key=key)
                        )
                issues.extend(_backend_location_issues(session.base, proposed))
            if step_key == "database":
                for key in ("store_database_name", "store_connection_name"):
                    if not _optional_text(proposed.get(key)):
                        issues.append(
                            ValidationIssue("A value is required.", field_key=key)
                        )
        elif target == LLM_SETUP_TARGET:
            if step_key == "provider":
                if not _optional_text(proposed.get("llm_provider_name")):
                    issues.append(
                        ValidationIssue(
                            "A provider name is required.", field_key="llm_provider_name"
                        )
                    )
                if _optional_text(proposed.get("llm_provider_kind")) not in supported_provider_keys():
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
                issues.extend(
                    _model_name_issues(
                        proposed,
                        kind_key="llm_provider_kind",
                        model_key="llm_model_choice",
                    )
                )
        return tuple(issues)

    def _candidate(self, session: _MutationSession) -> StackConfig:
        if session.target == CDM_SETUP_TARGET:
            set_dict = _cdm_set_dict(session.answers)
        elif session.target == MODEL_SETUP_TARGET:
            set_dict = _model_set_dict(session.answers)
        elif session.target == VECTOR_STORE_SETUP_TARGET:
            set_dict = _vector_store_set_dict(session.answers, session.base)
        elif session.target == LLM_SETUP_TARGET:
            set_dict = _llm_set_dict(session.answers)
        else:
            raise ValueError("Unsupported Groundworkers configuration target.")
        candidate = plan_configure(GroundworkersConfig, session.base, set_dict)
        groundworkers = GroundworkersConfig.validate_candidate(candidate)
        if session.target == CDM_SETUP_TARGET:
            candidate = _align_cdm_cli_configs(candidate, groundworkers)
        if session.target in {
            CDM_SETUP_TARGET,
            MODEL_SETUP_TARGET,
            VECTOR_STORE_SETUP_TARGET,
        }:
            candidate = _align_omop_emb_config(candidate, groundworkers)
        return candidate

    def _invalidate_plan(self, session: _MutationSession) -> None:
        if session.apply_token is not None:
            self._apply_tokens.pop(session.apply_token, None)
        session.apply_token = None
        session.candidate = None


def cdm_setup_workflow(
    operation: MutationOperation,
) -> ConfigWorkflowSpec:
    """Describe the reusable Groundworkers CDM setup workflow."""

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
                "registry",
                "Registered models",
                ("model_source",),
            ),
            ConfigWorkflowStep(
                "model",
                "Choose the model",
                ("model_entry_name", "model_choice"),
            ),
            ConfigWorkflowStep(
                "prefixes",
                "Set the embedding prefixes",
                ("prefix_convention",),
            ),
            ConfigWorkflowStep(
                "custom_prefixes",
                "Enter the prefixes",
                ("document_prefix", "query_prefix"),
                when=(
                    ConfigBranchCondition(
                        "prefix_convention",
                        frozenset({PREFIX_CONVENTION_CUSTOM}),
                    ),
                ),
            ),
        ),
    )


def vector_store_setup_workflow(
    operation: MutationOperation,
) -> ConfigWorkflowSpec:
    """Describe creating the embedding store and the database it lives in.

    Location is asked first and defaults to the CDM database, because that is
    what the rest of the tier already assumes: the embedding adapter enriches
    from ``cdm_engine`` and coverage counts the CDM's own concepts. Somewhere
    else is a real option, but it is the opt-in, and answering it is the only
    reason the database step exists.
    """

    return ConfigWorkflowSpec(
        key="groundworkers-vector-store",
        target=VECTOR_STORE_SETUP_TARGET,
        operation=operation,
        title="Configure the Groundworkers embedding store",
        purpose="Choose where vectors are stored, and the backend that stores them.",
        steps=(
            ConfigWorkflowStep(
                "location",
                "Where the vectors live",
                ("store_location",),
            ),
            ConfigWorkflowStep(
                "store",
                "Choose the backend",
                ("store_entry_name", "backend_type", "faiss_cache_dir"),
            ),
            ConfigWorkflowStep(
                "database",
                "Describe the separate database",
                ("store_database_name", "store_connection_name", "store_schema_name"),
                when=(
                    ConfigBranchCondition(
                        "store_location",
                        frozenset({STORE_LOCATION_SEPARATE}),
                    ),
                ),
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
    fresh = connection is None
    dialect = getattr(connection, "dialect", "postgresql+psycopg")
    return (
        FieldSpec(
            "connection_name",
            "Connection name (Advanced)",
            default=connection_name,
            help="Keep the generated name unless reusing or resolving a collision.",
        ),
        FieldSpec(
            "dialect",
            "Database type",
            kind=FieldKind.CHOICE,
            default=dialect,
            choices=(
                ChoiceOption("postgresql+psycopg", "PostgreSQL (recommended)"),
                ChoiceOption("sqlite", "SQLite (Advanced: existing file)"),
            ),
        ),
        FieldSpec(
            "host",
            "Host",
            required=False,
            default=getattr(connection, "host", "localhost"),
        ),
        FieldSpec(
            "port",
            "Port",
            kind=FieldKind.INTEGER,
            required=False,
            minimum=1,
            maximum=65535,
            default=getattr(connection, "port", 5432),
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
            default=getattr(connection, "database_name", None),
            help=(
                "Enter the PostgreSQL database containing populated OMOP vocabulary tables. "
                "For SQLite, select an existing CDM file."
            ),
        ),
        FieldSpec(
            "cdm_db_name",
            "CDM database entry (Advanced)",
            default=cdm_name,
            help="Keep the generated name unless reusing or resolving a collision.",
        ),
        FieldSpec(
            "schema_name",
            "CDM schema",
            default=getattr(database, "schema_name", "public" if fresh else "main"),
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


def _existing_sqlite_path(value: str, *, stack: StackConfig) -> bool:
    path = Path(value).expanduser()
    if not path.is_absolute() and stack.loaded_path is not None:
        path = stack.loaded_path.parent / path
    return path.is_file()


def _model_fields(stack: StackConfig) -> tuple[FieldSpec, ...]:
    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    model_name = _optional_text(tool.get("embedding_model_name"))
    model = stack.models.get(model_name) if model_name else None
    provider_name = getattr(model, "provider", "embedding_provider")
    provider = stack.providers.get(provider_name)
    provider_kind = getattr(provider, "provider", "ollama")
    existing_model = getattr(model, "model", None)
    pending = existing_model or "pending"
    stored_convention = _convention_for_stored_prefixes(
        getattr(model, "document_prefix", None),
        getattr(model, "query_prefix", None),
    )
    return (
        FieldSpec(
            "provider_name",
            "Provider entry (Advanced)",
            default=provider_name,
            help="Keep the generated name unless reusing or resolving a collision.",
        ),
        FieldSpec(
            "provider_kind",
            "Provider",
            kind=FieldKind.CHOICE,
            default=provider_kind,
            choices=_provider_choices(),
        ),
        FieldSpec(
            "base_url",
            "Provider endpoint",
            required=False,
            default=_default_endpoint(provider_kind, getattr(provider, "base_url", None)),
            help=_endpoint_help(provider_kind),
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
            "model_source",
            "Model source",
            kind=FieldKind.CHOICE,
            default=MODEL_SOURCE_REGISTERED,
            choices=(
                ChoiceOption(
                    MODEL_SOURCE_REGISTERED,
                    "Use a model already registered in the embedding store",
                ),
                ChoiceOption(
                    MODEL_SOURCE_NEW, "Register a new model from the provider"
                ),
            ),
            help="Populated from the store once the provider step is accepted.",
        ),
        FieldSpec(
            "model_entry_name",
            "Model entry (Advanced)",
            default=model_name or "embedding_model",
            help="Keep the generated name unless reusing or resolving a collision.",
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
        _prefix_convention_field(existing_model, default=stored_convention),
        FieldSpec(
            "document_prefix",
            "Document prefix",
            required=False,
            default=getattr(model, "document_prefix", None),
            help="Prepended to concept text as it is stored.",
        ),
        FieldSpec(
            "query_prefix",
            "Query prefix",
            required=False,
            default=getattr(model, "query_prefix", None),
            help="Prepended to search text at query time.",
        ),
    )


def _vector_store_fields(stack: StackConfig) -> tuple[FieldSpec, ...]:
    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    store_name = _optional_text(tool.get("vector_store_name"))
    store = stack.vector_stores.get(store_name) if store_name else None
    database_name = getattr(store, "database", "embedding_db")
    database = stack.databases.get(database_name)
    connection_name = getattr(database, "connection", None)
    _, cdm = _cdm_entry(stack)
    connections = tuple(stack.connections) or ("cdm_main",)
    location = _store_location_field(stack, database)
    return (
        location,
        FieldSpec(
            "store_entry_name",
            "Store entry (Advanced)",
            default=store_name or "embeddings",
            help="Keep the generated name unless reusing or resolving a collision.",
        ),
        FieldSpec(
            "backend_type",
            "Backend",
            kind=FieldKind.CHOICE,
            default=getattr(store, "backend_type", None)
            or _backend_for_stack(stack)
            or "sqlitevec",
            choices=_BACKEND_CHOICES,
        ),
        FieldSpec(
            "store_database_name",
            "Database entry (Advanced)",
            default=database_name,
            help="Keep the generated name unless reusing or resolving a collision.",
        ),
        FieldSpec(
            "store_connection_name",
            "Connection",
            kind=FieldKind.CHOICE,
            default=connection_name
            or getattr(cdm, "connection", None)
            or connections[0],
            choices=tuple(ChoiceOption(name, name) for name in connections),
            help="An existing connection. Add one through CDM setup or omop-config.",
        ),
        FieldSpec(
            "store_schema_name",
            "Schema",
            required=False,
            # No hardcoded fallback: 'public' is a PostgreSQL name, and it was
            # being offered for SQLite stores and for CDMs whose own schema is
            # something else. Blank means no override, which is right whenever
            # there is nothing better to copy.
            default=getattr(database, "schema_name", None)
            or getattr(cdm, "schema_name", None),
            help="Blank uses the connection's own default schema.",
        ),
        FieldSpec(
            "faiss_cache_dir",
            "FAISS cache directory",
            required=False,
            default=getattr(store, "faiss_cache_dir", None),
            help="Optional query-time cache. Leave blank unless using the embedding-faiss extra.",
        ),
    )


def _cdm_entry(stack: StackConfig) -> tuple[str, CDMDatabaseConfig | None]:
    """The CDM database entry Groundworkers is pointed at, and its name."""

    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    name = _optional_text(tool.get("cdm_db", "cdm_db")) or "cdm_db"
    entry = stack.databases.get(name)
    return name, entry if isinstance(entry, CDMDatabaseConfig) else None


def _store_location_field(
    stack: StackConfig,
    database: object | None,
) -> FieldSpec:
    """Offer the CDM database first, and only offer it when there is one.

    Preselects what the stored store already does, so rerunning setup on
    a configured stack does not silently propose to move the vectors.
    """

    cdm_name, cdm = _cdm_entry(stack)
    separate = ChoiceOption(
        STORE_LOCATION_SEPARATE, "Somewhere else (choose a connection and schema)"
    )
    if cdm is None:
        return FieldSpec(
            "store_location",
            "Vector storage",
            kind=FieldKind.CHOICE,
            default=STORE_LOCATION_SEPARATE,
            choices=(separate,),
            help=(
                "No CDM database is configured yet. Configure the CDM database first to "
                "store vectors alongside it."
            ),
        )
    stored = (
        STORE_LOCATION_CDM
        if database is not None and _matches_cdm(database, cdm)
        else STORE_LOCATION_SEPARATE
        if database is not None
        else STORE_LOCATION_CDM
    )
    return FieldSpec(
        "store_location",
        "Vector storage",
        kind=FieldKind.CHOICE,
        default=stored,
        choices=(
            ChoiceOption(
                STORE_LOCATION_CDM,
                f"In the CDM database ({cdm_name})",
            ),
            separate,
        ),
        help=(
            "The embedding tier already reads the CDM for concept text and coverage. "
            "Keeping the vectors there needs no further answers."
        ),
    )


def _matches_cdm(database: object, cdm: CDMDatabaseConfig) -> bool:
    return (
        getattr(database, "connection", None) == cdm.connection
        and getattr(database, "schema_name", None) == cdm.schema_name
    )


def _backend_type_field(
    stack: StackConfig,
    answers: Mapping[str, object],
) -> FieldSpec:
    """The backend choice, narrowed once the storage location is known."""

    stored = _optional_text(answers.get("backend_type")) or _stored_backend(stack)
    backend = _backend_for_stack(stack)
    if _store_location(answers) != STORE_LOCATION_CDM or backend is None:
        return FieldSpec(
            "backend_type",
            "Backend",
            kind=FieldKind.CHOICE,
            default=stored or backend or "sqlitevec",
            choices=_BACKEND_CHOICES,
        )
    return FieldSpec(
        "backend_type",
        "Backend",
        kind=FieldKind.CHOICE,
        default=backend,
        choices=tuple(item for item in _BACKEND_CHOICES if item.value == backend),
        help="Fixed by the CDM database's dialect, which is where the vectors go.",
    )


def _backend_location_issues(
    stack: StackConfig,
    answers: Mapping[str, object],
) -> tuple[ValidationIssue, ...]:
    """Refuse a backend the CDM database cannot serve.

    Only meaningful for the in-the-CDM location: elsewhere the store's own
    connection decides, and that connection is chosen a step later.
    """

    if _store_location(answers) != STORE_LOCATION_CDM:
        return ()
    required = _backend_for_stack(stack)
    backend = _optional_text(answers.get("backend_type"))
    if required is None or backend is None or backend == required:
        return ()
    return (
        ValidationIssue(
            f"The CDM database can only store vectors through {required!r}. "
            "Choose that backend, or store the vectors somewhere else.",
            field_key="backend_type",
        ),
    )


def _stored_backend(stack: StackConfig) -> str | None:
    """The backend the configured store already uses, if there is one."""

    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    name = _optional_text(tool.get("vector_store_name"))
    store = stack.vector_stores.get(name) if name else None
    return getattr(store, "backend_type", None)


def _backend_for_stack(stack: StackConfig) -> str | None:
    """The only omop-emb backend that can store vectors in the CDM's own database."""

    _, cdm = _cdm_entry(stack)
    if cdm is None:
        return None
    connection = stack.connections.get(cdm.connection)
    dialect = getattr(connection, "dialect", "")
    for prefix, backend in _BACKEND_FOR_DIALECT.items():
        if dialect.startswith(prefix):
            return backend
    return None


def _llm_fields(stack: StackConfig) -> tuple[FieldSpec, ...]:
    tool = stack.tools.get(GroundworkersConfig.tool_name, {})
    model_name = _optional_text(tool.get("llm_model_name"))
    model = stack.models.get(model_name) if model_name else None
    provider_name = getattr(model, "provider", "chat_provider")
    provider = stack.providers.get(provider_name)
    provider_kind = getattr(provider, "provider", "ollama")
    existing_model = getattr(model, "model", None)
    pending = existing_model or "pending"
    return (
        FieldSpec(
            "llm_provider_name",
            "Provider entry (Advanced)",
            default=provider_name,
            help="Keep the generated name unless reusing or resolving a collision.",
        ),
        FieldSpec(
            "llm_provider_kind",
            "Provider",
            kind=FieldKind.CHOICE,
            default=provider_kind,
            choices=_provider_choices(),
        ),
        FieldSpec(
            "llm_base_url",
            "Provider endpoint",
            required=False,
            default=_default_endpoint(provider_kind, getattr(provider, "base_url", None)),
            help=_endpoint_help(provider_kind),
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
            "llm_model_entry_name",
            "Model entry (Advanced)",
            default=model_name or "chat_model",
            help="Keep the generated name unless reusing or resolving a collision.",
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


def _vector_store_set_dict(
    answers: Mapping[str, object],
    stack: StackConfig,
) -> dict[str, object]:
    """Create the store, its generic database, and the reference to it.

    The connection is named rather than defined: a vector store uses a server
    that is already configured, and CDM setup creates connections.
    """
    store: dict[str, object] = {
        "name": str(answers["store_entry_name"]),
        "backend_type": str(answers["backend_type"]),
        "database": _store_database(answers, stack),
    }
    cache_dir = answers.get("faiss_cache_dir")
    if cache_dir not in (None, ""):
        store["faiss_cache_dir"] = cache_dir
    return {"vector_store_name": store}


def _store_database(
    answers: Mapping[str, object],
    stack: StackConfig,
) -> dict[str, object]:
    """The generic database entry the store's vectors live in.

    A vector store must reference a ``GenericDatabaseConfig`` and the CDM entry
    is a ``CDMDatabaseConfig``, so "in the CDM database" cannot be expressed by
    pointing at the CDM entry itself. It is expressed instead as a generic entry
    that copies the CDM's connection and schema, which is the same physical
    place.
    """
    if _store_location(answers) == STORE_LOCATION_CDM:
        cdm_name, cdm = _cdm_entry(stack)
        if cdm is None:
            raise ValueError(
                "Storing vectors in the CDM database needs a configured CDM database."
            )
        database: dict[str, object] = {
            "name": f"{cdm_name}{CDM_VECTOR_DATABASE_SUFFIX}",
            "kind": "generic",
            "connection": cdm.connection,
        }
        if cdm.schema_name:
            database["schema_name"] = cdm.schema_name
        return database
    database = {
        "name": str(answers["store_database_name"]),
        "kind": "generic",
        "connection": str(answers["store_connection_name"]),
    }
    schema = answers.get("store_schema_name")
    if schema not in (None, ""):
        database["schema_name"] = schema
    return database


def _store_location(answers: Mapping[str, object]) -> str:
    return _optional_text(answers.get("store_location")) or STORE_LOCATION_CDM


def _model_set_dict(answers: Mapping[str, object]) -> dict[str, object]:
    provider: dict[str, object] = {
        "name": str(answers["provider_name"]),
        "provider": str(answers["provider_kind"]),
    }
    for key in ("base_url", "api_key"):
        value = answers.get(key)
        if value not in (None, ""):
            provider[key] = value
    convention = _optional_text(answers.get("prefix_convention"))
    if convention == PREFIX_CONVENTION_CUSTOM:
        document_prefix = _prefix_text(answers.get("document_prefix"))
        query_prefix = _prefix_text(answers.get("query_prefix"))
    else:
        document_prefix, query_prefix = _prefix_pair(
            convention or PREFIX_CONVENTION_NONE
        )
    return {
        "embedding_model_name": {
            "name": str(answers["model_entry_name"]),
            "provider": provider,
            "model": str(answers["model_choice"]),
            "embeddings": True,
            # Written even when None, so a convention chosen once can be taken
            # back off the entry rather than being stuck there for good.
            "document_prefix": document_prefix,
            "query_prefix": query_prefix,
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
    if target == VECTOR_STORE_SETUP_TARGET:
        name = tool.get("vector_store_name")
        return isinstance(name, str) and name in stack.vector_stores
    if target == LLM_SETUP_TARGET:
        name = tool.get("llm_model_name")
        return isinstance(name, str) and name in stack.models
    raise ValueError("Groundworkers does not support this configuration target.")


def _align_omop_emb_config(
    stack: StackConfig,
    groundworkers: GroundworkersConfig | None = None,
) -> StackConfig:
    """Point omop-emb at the embedding resources Groundworkers will operate.

    ``oa-configurator`` gives each package its own typed ``[tools.<name>]``
    section. Packages share databases, models, and vector stores by having
    their ``RefTo`` fields name the same top-level entries. Groundworkers
    launches the omop-emb population CLI, so a complete Groundworkers embedding
    tier must also configure omop-emb to use that same tier.

    CDM, model, and vector-store setup are independently resumable. Until both
    optional embedding references exist, leave omop-emb untouched rather than
    creating a package section whose required references cannot validate.
    """

    resolved = groundworkers or GroundworkersConfig.validate_candidate(stack)
    if (
        resolved.embedding_model_name is None
        or resolved.vector_store_name is None
    ):
        return stack
    return plan_configure(
        _registered_package_config(_OMOP_EMB_TOOL_NAME),
        stack,
        {
            "cdm_db": resolved.cdm_db,
            "embedding_model_name": resolved.embedding_model_name,
            "vector_store_name": resolved.vector_store_name,
        },
    )


def _align_cdm_cli_configs(
    stack: StackConfig,
    groundworkers: GroundworkersConfig | None = None,
) -> StackConfig:
    """Point managed graph-maintenance CLIs at Groundworkers' CDM.

    Groundworkers supplies resolved dependencies directly to the omop-graph
    runtime, so runtime graph configuration stays Groundworkers-owned. Its setup
    console does, however, launch the omop-graph and omop-alchemy maintenance
    CLIs. Each CLI resolves its own typed package section and therefore needs
    its ``cdm_db`` reference bound to the same shared database entry.
    """

    resolved = groundworkers or GroundworkersConfig.validate_candidate(stack)
    aligned = stack
    for tool_name in _CDM_CLI_TOOL_NAMES:
        aligned = plan_configure(
            _registered_package_config(tool_name),
            aligned,
            {"cdm_db": resolved.cdm_db},
        )
    return aligned


def _registered_package_config(tool_name: str) -> type[PackageConfigBase]:
    """Load a package schema through oa-configurator's public plugin boundary."""

    matches = tuple(
        entry_point
        for entry_point in entry_points(group="omop.config")
        if entry_point.name == tool_name
    )
    if len(matches) != 1:
        raise ConfigurationError(
            f"Expected one omop.config entry point for {tool_name!r}; "
            f"found {len(matches)}."
        )
    config_class = matches[0].load()
    if not isinstance(config_class, type) or not issubclass(
        config_class, PackageConfigBase
    ):
        raise ConfigurationError(
            f"The omop.config entry point for {tool_name!r} is not a "
            "PackageConfigBase subclass."
        )
    if config_class.tool_name != tool_name:
        raise ConfigurationError(
            f"The omop.config entry point {tool_name!r} declares tool name "
            f"{config_class.tool_name!r}."
        )
    return config_class


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


def _sensitive_paths(model: BaseModel, prefix: str = "") -> set[str]:
    """Dotted paths into *model* whose field is declared ``Sensitive()``.

    Walks the models rather than the flattened dict `_flatten_stack` produces,
    so the schema's own declaration decides what the review diff masks, not the
    field's spelling. Paths line up with `_flatten_stack`'s, and a path for a
    value that `exclude_none` dropped is simply never looked up.

    `StackConfig.tools` is `dict[str, dict[str, Any]]`, so a package's own tool
    section carries no field metadata and is not walked. Secrets belong on the
    `[connections.*]` / `[providers.*]` entries a tool section references, which
    are typed and are covered here.
    """
    paths: set[str] = set()
    for name, info in type(model).model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        if is_sensitive(info):
            paths.add(path)
            continue
        paths |= _sensitive_paths_in(getattr(model, name, None), path)
    return paths


def _sensitive_paths_in(value: object, path: str) -> set[str]:
    if isinstance(value, BaseModel):
        return _sensitive_paths(value, path)
    if isinstance(value, Mapping):
        found: set[str] = set()
        for key, item in value.items():
            found |= _sensitive_paths_in(item, f"{path}.{key}")
        return found
    if isinstance(value, list | tuple):
        found = set()
        for index, item in enumerate(value):
            found |= _sensitive_paths_in(item, f"{path}.{index}")
        return found
    return set()


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
    if session.target == VECTOR_STORE_SETUP_TARGET:
        store_name = str(session.answers.get("store_entry_name", "embeddings"))
        # Named the same way `_store_database` names it, so the effect an operator
        # is shown is the entry the apply actually writes.
        database_name = str(
            _store_database(session.answers, session.base).get(
                "name", "embedding_db"
            )
        )
        return (
            EffectRef(
                session.operation.value,
                session.target,
                "embedding database",
                ConfigTarget(ConfigTargetKind.DATABASE, database_name, database_name),
                "databases",
            ),
            EffectRef(
                session.operation.value,
                session.target,
                "embedding store",
                ConfigTarget(
                    ConfigTargetKind.VECTOR_STORE, store_name, store_name
                ),
                "vector_stores",
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


PREFIX_CONVENTION_NONE: Final = "none"
PREFIX_CONVENTION_CUSTOM: Final = "custom"

_PREFIX_CONVENTIONS: Final = (
    (
        PREFIX_CONVENTION_NONE,
        "No prefixes (symmetric model)",
        None,
        None,
        (),
    ),
    (
        "nomic",
        "search_document: / search_query:  (Nomic)",
        "search_document: ",
        "search_query: ",
        ("nomic",),
    ),
    (
        "e5",
        "passage: / query:  (E5, multilingual-e5)",
        "passage: ",
        "query: ",
        ("e5",),
    ),
    (
        "query_only",
        "query:  on queries only (Snowflake Arctic Embed 2.0)",
        None,
        "query: ",
        ("arctic-embed",),
    ),
    (
        "bge",
        "Query-only instruction (BGE)",
        None,
        "Represent this sentence for searching relevant passages: ",
        ("bge", "mxbai"),
    ),
    (
        PREFIX_CONVENTION_CUSTOM,
        "Enter prefixes manually",
        None,
        None,
        (),
    ),
)
"""Prefix pairs for asymmetric embedding models, keyed by convention.

``omop_llm`` publishes the recognised prefixes as a flat set, but they are only
correct in matched pairs: indexing with ``search_document: `` and querying with
``query: `` produces embeddings that look fine and retrieve badly, with nothing
raised at any point. Offering the pairs, rather than two free-text boxes, is
what removes that failure mode. ``test_offered_prefixes_are_the_ones_omop_llm_
recognises`` keeps this table honest against the upstream set.

The trailing tuple holds model-name fragments used only to preselect a
convention. It never decides anything on its own: the choice is on a step the
operator has to pass through, and the resulting prefixes are named on the
review, so a wrong guess is visible and correctable before it is written.

Fragments are deliberately narrow. Arctic Embed is the reason: 1.0 used the BGE
instruction, 2.0 uses a bare ``query: `` on queries only, and neither is the
Nomic pair its name sits next to in most listings. A family whose convention is
not unambiguous from the name is better left unmatched -- an operator choosing
from the list has the model card in front of them, and a confident wrong default
is the exact failure this step exists to prevent.
"""


def _prefix_pair(convention: str) -> tuple[str | None, str | None]:
    for key, _label, document, query, _fragments in _PREFIX_CONVENTIONS:
        if key == convention:
            return document, query
    return None, None


def _default_prefix_convention(model_name: str | None) -> str:
    """Preselect the convention the chosen model is usually trained with."""
    lowered = (model_name or "").lower()
    for key, _label, _document, _query, fragments in _PREFIX_CONVENTIONS:
        if any(fragment in lowered for fragment in fragments):
            return key
    return PREFIX_CONVENTION_NONE


def _convention_for_stored_prefixes(
    document_prefix: str | None,
    query_prefix: str | None,
) -> str | None:
    """Recognise prefixes already in the config so an update does not reset them."""
    if document_prefix is None and query_prefix is None:
        return None
    for key, _label, document, query, _fragments in _PREFIX_CONVENTIONS:
        if key == PREFIX_CONVENTION_CUSTOM:
            continue
        if (document, query) == (document_prefix, query_prefix):
            return key
    return PREFIX_CONVENTION_CUSTOM


def _prefix_convention_field(model_name: str | None, *, default: str | None) -> FieldSpec:
    return FieldSpec(
        "prefix_convention",
        "Prefix convention",
        kind=FieldKind.CHOICE,
        default=default or _default_prefix_convention(model_name),
        choices=tuple(
            ChoiceOption(key, label) for key, label, _d, _q, _f in _PREFIX_CONVENTIONS
        ),
        help=(
            "Asymmetric models embed documents and queries with different "
            "prefixes. The wrong pair retrieves badly without any error, and "
            "changing it later means re-embedding everything."
        ),
    )


def _model_name_issues(
    proposed: Mapping[str, object],
    *,
    kind_key: str,
    model_key: str,
) -> tuple[ValidationIssue, ...]:
    """Apply the provider's own naming rules before the entry can be written.

    ``omop_llm`` refuses names that would make stored vectors untrustworthy --
    an Ollama ``:latest`` tag being the case that matters, since re-pulling the
    model silently changes what the stored embeddings mean. That rule used to
    fire only when something downstream built a backend, which is far too late:
    the entry is already saved and the operator sees a failure on an unrelated
    screen. Raising it here attaches it to the field that caused it.

    The provider's message is forwarded verbatim. These are authored rule
    explanations naming a model and a tag, not the provider-endpoint errors
    elsewhere in this module that are withheld for carrying credentials.
    """
    kind = _optional_text(proposed.get(kind_key))
    model = _optional_text(proposed.get(model_key))
    if kind is None or model is None:
        return ()
    try:
        canonical_model_name(kind, model)
    except ValueError as exc:
        return (ValidationIssue(str(exc), field_key=model_key),)
    except Exception:
        # Broad except: an unsupported provider is already reported by the
        # provider step, and no naming rule exists to apply here.
        return ()
    return ()


def _prefix_text(value: object) -> str | None:
    """Keep an embedding prefix exactly as typed, trailing space included.

    ``_optional_text`` strips, which is right for a name and wrong here:
    ``"search_document: "`` and ``"search_document:"`` are different prefixes,
    and losing the space silently changes what every stored vector means.
    """
    if value is None:
        return None
    text = str(value)
    return text or None


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



# Derived from omop-llm rather than hand-listed. A hardcoded list previously
# offered "openai-compatible", which any-llm does not recognise, so a chat
# provider configured through the console failed at runtime with
# UnsupportedProviderError instead of at the point of choosing it.
_PROVIDER_LABELS: Final = {
    "ollama": "Ollama",
    "openai": "OpenAI-compatible",
    "vllm": "vLLM",
    "llamacpp": "llama.cpp",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
}


def _provider_choices() -> tuple[ChoiceOption, ...]:
    return tuple(
        ChoiceOption(key, _PROVIDER_LABELS.get(key, key))
        for key in supported_providers()
    )


def supported_provider_keys() -> frozenset[str]:
    return frozenset(supported_providers())


# Endpoints a provider serves at by default. Only providers that run locally on
# a well-known port get one; a hosted provider's own default is better than any
# guess, and omop-llm falls back to it when base_url is unset.
_PROVIDER_ENDPOINT_DEFAULTS: Final = {
    "ollama": "http://localhost:11434",
}


def _default_endpoint(provider_kind: str | None, stored: str | None) -> str | None:
    if stored:
        return stored
    return _PROVIDER_ENDPOINT_DEFAULTS.get(provider_kind or "")


def _endpoint_help(provider_kind: str | None) -> str:
    if provider_kind == "ollama":
        return "Ollama server root, for example http://localhost:11434. Blank uses the provider's own default."
    if provider_kind in ("openai", "vllm", "llamacpp"):
        return (
            "OpenAI-compatible API root, for example http://localhost:8000. A trailing "
            "/v1 is added automatically if you leave it off. Blank uses the provider's "
            "own default."
        )
    if provider_kind == "anthropic":
        return (
            "Anthropic API root. Blank uses Anthropic's own default "
            "(https://api.anthropic.com)."
        )
    if provider_kind == "gemini":
        return "Gemini API root. Blank uses Google's own default."
    return "Provider API root. Blank uses the provider's own default."


def _registry_summary(store: EmbeddingStoreSnapshot) -> str:
    """One line describing what the embedding store holds, for the choice's help."""
    if store.state is EmbeddingStoreState.UNCONFIGURED:
        return (
            "No embedding store is configured, so nothing is registered yet. "
            "Configure one from the Databases table."
        )
    if not store.reachable:
        return "The embedding store could not be reached, so its registry is unknown."
    if not store.models:
        return "The embedding store is reachable but has no registered models yet."
    return "Registered: " + "; ".join(
        f"{model.model_name} ({model.dimensions}d, "
        f"{'has vectors' if model.has_embeddings else 'no vectors yet'})"
        for model in store.models
    )
