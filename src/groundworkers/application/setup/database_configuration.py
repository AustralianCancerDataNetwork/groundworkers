"""
This file is primarily to support the Groundworkers setup wizard, which is a guided interface for configuring the OMOP database connection and related resources. 
It provides functions to plan, draft, and apply database configuration changes, as well as to validate and verify the configuration against the current stack snapshot. 
The code also includes data classes to represent the configuration state and any issues encountered during setup.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from oa_configurator import (
    DatabaseConfig,
    ResourceConfig,
    StackConfig,
    ToolConfig,
)
from pydantic import ValidationError

from groundworkers.application.setup.configuration import save_configuration
from groundworkers.application.setup.models import (
    ConfigurationSaveResult,
    ConfigurationSnapshot,
    DatabaseTarget,
    SetupIssue,
)
from groundworkers.config import GroundworkersConfig, resolve_cdm_resource_name

DEFAULT_RESOURCE_NAME = "cdm_db"
DEFAULT_CONNECTION_NAME = "cdm_main"
SUPPORTED_DIALECTS = (
    "postgresql+psycopg",
    "sqlite",
    # not yet supported, but could be added in the future - duckdb only one planned on the roadmap currently:
    # "mssql+pyodbc",
    # "oracle+oracledb",
    # "duckdb",
)


class ConnectionStrategy(StrEnum):
    REUSE = "reuse"
    CREATE = "create"
    EDIT = "edit"
    CLONE = "clone"


class PasswordAction(StrEnum):
    PRESERVE = "preserve"
    SET = "set"
    CLEAR = "clear"


@dataclass(frozen=True)
class ConnectionReference:
    resource_name: str
    role: str


@dataclass(frozen=True)
class DatabaseConfigurationPlan:
    path: str
    revision: str | None
    editable: bool
    read_only_reason: str | None
    resource_name: str
    connection_name: str
    resource_names: tuple[str, ...]
    connection_names: tuple[str, ...]
    shared_connection_references: tuple[ConnectionReference, ...]
    target: DatabaseTarget | None
    issues: tuple[SetupIssue, ...] = ()

    @property
    def shared_connection(self) -> bool:
        return len(self.shared_connection_references) > 1


@dataclass(frozen=True)
class DatabaseConfigurationDraft:
    resource_name: str = DEFAULT_RESOURCE_NAME
    connection_strategy: ConnectionStrategy = ConnectionStrategy.CREATE
    connection_name: str = DEFAULT_CONNECTION_NAME
    source_connection_name: str | None = None
    dialect: str = "postgresql+psycopg"
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    password_action: PasswordAction = PasswordAction.PRESERVE
    database_name: str | None = None
    read_only: bool = True
    test_only: bool = False
    cdm_schema: str = "cdm"
    vocabulary_connection_name: str | None = None
    vocabulary_schema: str | None = None
    results_schema: str | None = None

    def safe_for_display(self) -> dict[str, object]:
        return {
            "resource_name": self.resource_name,
            "connection_strategy": self.connection_strategy.value,
            "connection_name": self.connection_name,
            "source_connection_name": self.source_connection_name,
            "dialect": self.dialect,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": "configured" if self.password else None,
            "password_action": self.password_action.value,
            "database_name": self.database_name,
            "read_only": self.read_only,
            "test_only": self.test_only,
            "cdm_schema": self.cdm_schema,
            "vocabulary_connection_name": self.vocabulary_connection_name,
            "vocabulary_schema": self.vocabulary_schema,
            "results_schema": self.results_schema,
        }


@dataclass(frozen=True)
class DatabaseConfigurationApplyResult:
    save_result: ConfigurationSaveResult
    changed_fields: tuple[str, ...]


def plan_database_configuration(
    snapshot: ConfigurationSnapshot,
) -> DatabaseConfigurationPlan:
    """Translate the current oa-configurator schema into setup concepts."""

    if snapshot.stack is None:
        return DatabaseConfigurationPlan(
            path=str(snapshot.path),
            revision=snapshot.revision,
            editable=snapshot.ownership.editable,
            read_only_reason=(
                None
                if snapshot.ownership.editable
                else snapshot.ownership.guidance
            ),
            resource_name=DEFAULT_RESOURCE_NAME,
            connection_name=DEFAULT_CONNECTION_NAME,
            resource_names=(),
            connection_names=(),
            shared_connection_references=(),
            target=None,
            issues=snapshot.issues,
        )

    stack = snapshot.stack
    resource_name = _current_resource_name(snapshot)
    resource = stack.resources.get(_resolved_resource_name(stack, resource_name))
    connection_name = resource.database if resource is not None else DEFAULT_CONNECTION_NAME
    read_only_reason = _read_only_reason(snapshot, resource_name, connection_name)
    target = _target_from_stack(stack, resource_name) if resource is not None else None
    return DatabaseConfigurationPlan(
        path=str(snapshot.path),
        revision=snapshot.revision,
        editable=snapshot.ownership.editable and read_only_reason is None,
        read_only_reason=read_only_reason
        or (None if snapshot.ownership.editable else snapshot.ownership.guidance),
        resource_name=resource_name,
        connection_name=connection_name,
        resource_names=tuple(sorted(stack.resources)),
        connection_names=tuple(sorted(stack.databases)),
        shared_connection_references=connection_references(stack, connection_name),
        target=target,
        issues=snapshot.issues,
    )


def draft_from_plan(plan: DatabaseConfigurationPlan, snapshot: ConfigurationSnapshot) -> DatabaseConfigurationDraft:
    if snapshot.stack is None:
        return DatabaseConfigurationDraft(
            resource_name=plan.resource_name,
            connection_name=plan.connection_name,
        )
    stack = snapshot.stack
    resource = stack.resources.get(_resolved_resource_name(stack, plan.resource_name))
    database = stack.databases.get(plan.connection_name)
    if resource is None or database is None:
        return DatabaseConfigurationDraft(
            resource_name=plan.resource_name,
            connection_name=plan.connection_name,
        )
    return DatabaseConfigurationDraft(
        resource_name=plan.resource_name,
        connection_strategy=ConnectionStrategy.EDIT,
        connection_name=plan.connection_name,
        source_connection_name=plan.connection_name,
        dialect=database.dialect,
        host=database.host,
        port=database.port,
        user=database.user,
        database_name=database.database_name,
        read_only=database.read_only,
        test_only=database.test_only,
        cdm_schema=resource.cdm_schema,
        vocabulary_connection_name=resource.vocab_database,
        vocabulary_schema=resource.vocab_schema,
        results_schema=resource.results_schema,
    )


def apply_database_configuration(
    snapshot: ConfigurationSnapshot,
    draft: DatabaseConfigurationDraft,
) -> DatabaseConfigurationApplyResult:
    """Persist a current-schema database configuration candidate safely."""

    plan = plan_database_configuration(snapshot)
    if not plan.editable:
        raise PermissionError(
            plan.read_only_reason
            or "This configuration cannot be edited through the setup console."
        )

    original = snapshot.stack
    stack = _editable_stack(snapshot)
    before = stack.model_dump(mode="python")
    resource_name = _base_resource_name(stack, draft.resource_name)
    connection_name = draft.connection_name.strip() or DEFAULT_CONNECTION_NAME
    database = _database_from_draft(stack, draft)

    stack.databases[connection_name] = database
    stack.resources[resource_name] = ResourceConfig(
        database=connection_name,
        vocab_database=draft.vocabulary_connection_name or None,
        cdm_schema=draft.cdm_schema,
        vocab_schema=draft.vocabulary_schema or None,
        results_schema=draft.results_schema or None,
    )
    stack.tools[GroundworkersConfig.tool_name] = _groundworkers_tool(stack, resource_name)

    try:
        StackConfig.model_validate(stack.model_dump(mode="python"))
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    result = save_configuration(
        stack,
        path=snapshot.path,
        expected_revision=snapshot.revision,
        ownership=snapshot.ownership,
    )
    changed = _changed_top_level(before, result.snapshot.stack, original)
    return DatabaseConfigurationApplyResult(save_result=result, changed_fields=changed)


def connection_references(
    stack: StackConfig,
    connection_name: str,
) -> tuple[ConnectionReference, ...]:
    references: list[ConnectionReference] = []
    for resource_name, resource in sorted(stack.resources.items()):
        if resource.database == connection_name:
            references.append(ConnectionReference(resource_name, "cdm"))
        if resource.vocab_database == connection_name:
            references.append(ConnectionReference(resource_name, "vocabulary"))
    return tuple(references)


def candidate_targets(
    draft: DatabaseConfigurationDraft,
) -> tuple[DatabaseTarget, ...]:
    """Build read-only verification targets from a draft without persisting it."""

    database = _new_database_from_draft(draft, existing=None)
    resource = ResourceConfig(
        database=draft.connection_name,
        vocab_database=draft.vocabulary_connection_name or None,
        cdm_schema=draft.cdm_schema,
        vocab_schema=draft.vocabulary_schema or None,
        results_schema=draft.results_schema or None,
    )
    return (
        DatabaseTarget(
            key="database.candidate.cdm",
            label="Candidate CDM / vocabulary",
            resource_name=draft.resource_name,
            database_name=draft.connection_name,
            safe_url=database.safe_url(),
            cdm_schema=resource.cdm_schema,
            vocabulary_schema=resource.vocab_schema or resource.cdm_schema,
            connection_url=database.build_url(),
        ),
    )


def update_draft(
    draft: DatabaseConfigurationDraft,
    **changes: object,
) -> DatabaseConfigurationDraft:
    coerced = dict(changes)
    if "connection_strategy" in coerced and not isinstance(
        coerced["connection_strategy"], ConnectionStrategy
    ):
        coerced["connection_strategy"] = ConnectionStrategy(str(coerced["connection_strategy"]))
    if "password_action" in coerced and not isinstance(
        coerced["password_action"], PasswordAction
    ):
        coerced["password_action"] = PasswordAction(str(coerced["password_action"]))
    return replace(draft, **coerced)


def _editable_stack(snapshot: ConfigurationSnapshot) -> StackConfig:
    if snapshot.stack is None:
        return StackConfig()
    return StackConfig.model_validate(snapshot.stack.model_dump(mode="python"))


def _database_from_draft(
    stack: StackConfig,
    draft: DatabaseConfigurationDraft,
) -> DatabaseConfig:
    existing_name = draft.source_connection_name or draft.connection_name
    existing = stack.databases.get(existing_name)
    if draft.connection_strategy is ConnectionStrategy.REUSE:
        reused = stack.databases.get(draft.connection_name)
        if reused is None:
            raise ValueError(f"Connection {draft.connection_name!r} is not configured.")
        return reused
    return _new_database_from_draft(draft, existing=existing)


def _new_database_from_draft(
    draft: DatabaseConfigurationDraft,
    *,
    existing: DatabaseConfig | None,
) -> DatabaseConfig:
    password = existing.password if existing is not None else None
    if draft.password_action is PasswordAction.CLEAR:
        password = None
    elif draft.password_action is PasswordAction.SET:
        password = draft.password

    return DatabaseConfig(
        dialect=draft.dialect,
        host=None if draft.dialect.startswith("sqlite") else draft.host,
        port=None if draft.dialect.startswith("sqlite") else draft.port,
        user=None if draft.dialect.startswith("sqlite") else draft.user,
        password=None if draft.dialect.startswith("sqlite") else password,
        database_name=draft.database_name or (":memory:" if draft.dialect.startswith("sqlite") else None),
        read_only=draft.read_only,
        test_only=draft.test_only,
    )


def _groundworkers_tool(stack: StackConfig, resource_name: str) -> ToolConfig:
    existing = stack.tools.get(GroundworkersConfig.tool_name)
    extra = dict(existing.extra) if existing is not None else {}
    return ToolConfig(default_resource=resource_name, extra=extra)


def _current_resource_name(snapshot: ConfigurationSnapshot) -> str:
    if snapshot.usable and snapshot.stack is not None:
        try:
            return resolve_cdm_resource_name(snapshot.stack)
        except Exception:  # noqa: BLE001 - fall back to the conventional name
            pass
    return DEFAULT_RESOURCE_NAME


def _base_resource_name(stack: StackConfig, resource_name: str) -> str:
    return stack.resource_aliases.get(resource_name, resource_name)


def _resolved_resource_name(stack: StackConfig, resource_name: str) -> str:
    return stack.resource_aliases.get(resource_name, resource_name)


def _target_from_stack(stack: StackConfig, resource_name: str) -> DatabaseTarget:
    base_resource_name = _resolved_resource_name(stack, resource_name)
    resource = stack.resources[base_resource_name]
    database = stack.databases[resource.database]
    return DatabaseTarget(
        key="database.cdm",
        label="CDM / vocabulary",
        resource_name=resource_name,
        database_name=resource.database,
        safe_url=database.safe_url(),
        cdm_schema=resource.cdm_schema,
        vocabulary_schema=resource.vocab_schema or resource.cdm_schema,
        connection_url=database.build_url(),
    )


def _read_only_reason(
    snapshot: ConfigurationSnapshot,
    resource_name: str,
    connection_name: str,
) -> str | None:
    if not snapshot.ownership.editable:
        return snapshot.ownership.guidance
    stack = snapshot.stack
    if stack is None or stack.active_profile is None:
        return None
    profile = stack.profiles.get(stack.active_profile)
    if profile is None:
        return None
    if GroundworkersConfig.tool_name in profile.tools:
        return (
            "The active profile overrides the Groundworkers resource selection. "
            "Edit the controlling profile outside this setup wizard."
        )
    base_resource_name = _resolved_resource_name(stack, resource_name)
    if base_resource_name in profile.resources:
        return (
            "The active profile overrides this OMOP database mapping. "
            "Base configuration writes are disabled for this target."
        )
    if connection_name in profile.databases:
        return (
            "The active profile overrides this physical connection. "
            "Base configuration writes are disabled for this target."
        )
    return None


def _changed_top_level(
    before: dict[str, object],
    reloaded: StackConfig | None,
    original: StackConfig | None,
) -> tuple[str, ...]:
    if reloaded is None:
        return ()
    after = reloaded.model_dump(mode="python")
    return tuple(
        key
        for key in ("databases", "resources", "tools", "resource_aliases")
        if before.get(key) != after.get(key)
        or (original is None and after.get(key))
    )
