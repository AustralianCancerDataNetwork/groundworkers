"""Generic oa-configurator-backed configuration for Groundworkers plugins.

Groundskeeping owns the portable wizard lifecycle. This module owns the OA
schema reflection, recursive ``RefTo`` form, private ``StackConfig`` candidate,
and revision-aware persistence needed to configure any supported
``PackageConfigBase`` without plugin-specific UI code.
"""

from __future__ import annotations

import secrets
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from groundskeeping.configurator import (
    ConfigApplyIntent,
    ConfigApplyResult,
    ConfigApplyStatus,
    ConfigBranchCondition,
    ConfigDraft,
    ConfigPlan,
    ConfigStepResult,
    ConfigTarget,
    ConfigTargetKind,
    ConfigWorkflowSpec,
    ConfigWorkflowStep,
    ConfigWorkflowStepKind,
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
    CDMDatabaseConfig,
    ConnectionConfig,
    GenericDatabaseConfig,
    ModelConfig,
    PackageConfigBase,
    ProviderConfig,
    RefTo,
    StackConfig,
    VectorStoreConfig,
    is_sensitive,
    plan_configure,
)
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

from groundworkers.application.setup.configuration import (
    ConfigurationConflictError,
    load_configuration,
    save_configuration,
)
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationState,
)

_CREATE = "__create_new__"
_NONE = "__not_configured__"
_SEPARATOR = "::"
_REFERENCE_SECTIONS: dict[type[BaseModel], str] = {
    ConnectionConfig: "connections",
    GenericDatabaseConfig: "databases",
    CDMDatabaseConfig: "databases",
    ProviderConfig: "providers",
    ModelConfig: "models",
    VectorStoreConfig: "vector_stores",
}


class UnsupportedPluginConfigSchema(TypeError):
    """A Tier-A plugin field cannot be represented by the generic wizard."""


@dataclass(frozen=True)
class _Binding:
    key: str
    path: tuple[str, ...]
    kind: Literal["scalar", "reference", "name"]
    section: str | None = None


@dataclass(frozen=True)
class _PluginForm:
    fields: tuple[FieldSpec, ...]
    steps: tuple[ConfigWorkflowStep, ...]
    bindings: Mapping[str, _Binding]


@dataclass(repr=False)
class _Session:
    target: ConfigTarget
    operation: MutationOperation
    expected_revision: str
    base: StackConfig
    fields: tuple[FieldSpec, ...]
    bindings: Mapping[str, _Binding]
    answers: dict[str, object] = field(default_factory=dict)
    changed_fields: frozenset[str] = frozenset()
    candidate: StackConfig | None = None
    apply_token: str | None = None


def plugin_config_target(
    config_cls: type[PackageConfigBase],
) -> ConfigTarget:
    """Return the stable target shared by a plugin's workflow and provider."""

    title = config_cls.tool_name.replace("_", " ").replace("-", " ").title()
    return ConfigTarget(
        ConfigTargetKind.TOOL,
        f"gw_plugin-{config_cls.tool_name}",
        title,
    )


class PackageConfigMutationService:
    """Configure one ``PackageConfigBase`` through the generic wizard."""

    def __init__(
        self,
        config_path: str | Path,
        config_cls: type[PackageConfigBase],
        *,
        ownership: ConfigurationOwnership | None = None,
        on_applied: Callable[[], None] | None = None,
    ) -> None:
        self._path = Path(config_path).expanduser().resolve()
        self._config_cls = config_cls
        self._target = plugin_config_target(config_cls)
        self._ownership = ownership or ConfigurationOwnership()
        self._on_applied = on_applied
        self._form: _PluginForm | None = None
        self._sessions: dict[str, _Session] = {}
        self._apply_tokens: dict[str, str] = {}

    @property
    def target(self) -> ConfigTarget:
        return self._target

    def workflow(self, operation: MutationOperation) -> ConfigWorkflowSpec:
        """Build the scalar/reference workflow from the current OA stack."""

        try:
            base, _ = self._load_base()
        except UnavailableMutationService:
            # The TUI page registry is built before a missing base configuration
            # has necessarily been created. The field shape is still knowable;
            # capabilities()/begin() remain the authority on current availability.
            base = StackConfig.for_session()
        self._form = _FormBuilder(base, self._config_cls).build()
        return ConfigWorkflowSpec(
            key=f"plugin-{self._config_cls.tool_name}-{operation.value}",
            target=self._target,
            operation=operation,
            title=f"Configure {self._target.title}",
            purpose=(
                "Configure package settings and reuse or create referenced stack entries."
            ),
            steps=self._form.steps,
            apply_label="Save configuration",
        )

    def capabilities(
        self, target: ConfigTarget, operation: MutationOperation
    ) -> MutationCapabilities:
        if target != self._target:
            return MutationCapabilities(
                target,
                operation,
                False,
                "This provider does not serve that plugin target.",
            )
        if not self._ownership.editable:
            return MutationCapabilities(
                target,
                operation,
                False,
                f"{self._ownership.source_label}: {self._ownership.guidance}",
            )
        try:
            base, _ = self._load_base()
            _FormBuilder(base, self._config_cls).build()
        except UnsupportedPluginConfigSchema as exc:
            return MutationCapabilities(target, operation, False, str(exc))
        return MutationCapabilities(target, operation, True)

    def begin(self, target: ConfigTarget, operation: MutationOperation) -> ConfigDraft:
        capabilities = self.capabilities(target, operation)
        if not capabilities.supported:
            raise MutationOperationUnsupported(
                capabilities.reason or "Plugin configuration is unsupported."
            )
        base, revision = self._load_base()
        form = self._form or _FormBuilder(base, self._config_cls).build()
        token = secrets.token_urlsafe(24)
        self._sessions[token] = _Session(
            target=target,
            operation=operation,
            expected_revision=revision,
            base=base,
            fields=form.fields,
            bindings=form.bindings,
        )
        return ConfigDraft(target, operation, token, expected_revision=revision)

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
        specs = {spec.key: spec for spec in session.fields}
        parsed: dict[str, object] = {}
        issues: list[ValidationIssue] = []
        for key, raw in values.items():
            spec = specs.get(key)
            if spec is None:
                issues.append(ValidationIssue(f"Unknown field {key!r}.", key))
                continue
            if spec.masks_value and raw in (None, ""):
                # An empty secret widget means "leave the stored value alone".
                # Tier A deliberately does not guess whether blank means clear.
                continue
            try:
                parsed[key] = spec.parse(raw).value
            except ValueError as exc:
                issues.append(ValidationIssue(str(exc), key))
        if issues:
            return ConfigStepResult(tuple(issues), session.changed_fields)

        for key in discard_fields:
            session.answers.pop(key, None)
        session.answers.update(parsed)
        session.changed_fields = frozenset(session.answers)
        self._invalidate_plan(session)
        return ConfigStepResult(changed_fields=session.changed_fields)

    def plan(self, draft: ConfigDraft) -> ConfigPlan:
        session = self._session(draft)
        self._invalidate_plan(session)
        try:
            name_issues = _new_name_issues(
                session.base,
                session.answers,
                session.bindings,
            )
            if name_issues:
                return ConfigPlan(
                    session.target,
                    session.operation,
                    build_config_diff(session.target, {}, {}),
                    issues=name_issues,
                    expected_revision=session.expected_revision,
                )
            set_dict = _nested_set_dict(session.answers, session.bindings)
            candidate = plan_configure(self._config_cls, session.base, set_dict)
        except (ValidationError, ValueError, TypeError):
            return ConfigPlan(
                session.target,
                session.operation,
                build_config_diff(session.target, {}, {}),
                issues=(
                    ValidationIssue(
                        "The proposed plugin configuration is incomplete or invalid."
                    ),
                ),
                expected_revision=session.expected_revision,
            )

        original = _flatten_stack(session.base)
        planned = _flatten_stack(candidate)
        sensitive = _sensitive_paths(session.base) | _sensitive_paths(candidate)
        for name, info in self._config_cls.model_fields.items():
            if is_sensitive(info):
                sensitive.add(f"tools.{self._config_cls.tool_name}.{name}")
        diff = build_config_diff(
            session.target,
            original,
            planned,
            sensitive_fields=frozenset(sensitive),
        )
        apply_token = secrets.token_urlsafe(24)
        session.candidate = candidate
        session.apply_token = apply_token
        self._apply_tokens[apply_token] = draft.session_token
        return ConfigPlan(
            session.target,
            session.operation,
            diff,
            apply_token=apply_token,
            expected_revision=session.expected_revision,
        )

    def apply(self, intent: ConfigApplyIntent) -> ConfigApplyResult:
        session_token = self._apply_tokens.pop(intent.apply_token, None)
        if session_token is None:
            return ConfigApplyResult(
                ConfigApplyStatus.REJECTED,
                "This plugin configuration plan is no longer available.",
            )
        session = self._sessions.get(session_token)
        if session is None:
            return ConfigApplyResult(
                ConfigApplyStatus.REJECTED,
                "This plugin configuration session has closed.",
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
                "The apply request does not match its prepared plugin plan.",
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
                "Configuration changed before this plugin plan could be applied.",
                "Reload the configuration, review the new state, and try again.",
            )
        except (ValidationError, ValueError):
            return ConfigApplyResult(
                ConfigApplyStatus.REJECTED,
                "The plugin configuration change was rejected.",
                "Review the current configuration and prepare a new plan.",
            )
        except Exception:
            return ConfigApplyResult(
                ConfigApplyStatus.FAILED,
                "The plugin configuration could not be saved.",
                "The previous configuration remains authoritative.",
            )
        self._sessions.pop(session_token, None)
        if self._on_applied is not None:
            self._on_applied()
        return ConfigApplyResult(
            ConfigApplyStatus.APPLIED,
            f"{self._target.title} configuration was updated.",
            refresh_pages=frozenset({self._target.key}),
        )

    def cancel(self, draft: ConfigDraft) -> None:
        session = self._sessions.pop(draft.session_token, None)
        if session is not None:
            self._invalidate_plan(session)
            session.answers.clear()

    def _load_base(self) -> tuple[StackConfig, str]:
        snapshot = load_configuration(
            config_path=self._path,
            ownership=self._ownership,
        )
        if snapshot.state is ConfigurationState.MALFORMED:
            raise UnavailableMutationService(
                "The configuration file must be repaired before plugins can be configured."
            )
        if snapshot.stack is None or snapshot.revision is None:
            raise UnavailableMutationService(
                "Create the base Groundworkers configuration before configuring plugins."
            )
        return snapshot.stack, snapshot.revision

    def _session(self, draft: ConfigDraft) -> _Session:
        session = self._sessions.get(draft.session_token)
        if session is None:
            raise ValueError("The plugin configuration session is unavailable.")
        if (
            draft.target != session.target
            or draft.operation is not session.operation
            or draft.expected_revision != session.expected_revision
        ):
            raise ValueError("The draft does not match its plugin configuration session.")
        return session

    def _invalidate_plan(self, session: _Session) -> None:
        if session.apply_token is not None:
            self._apply_tokens.pop(session.apply_token, None)
        session.apply_token = None
        session.candidate = None


class _FormBuilder:
    def __init__(self, stack: StackConfig, config_cls: type[PackageConfigBase]) -> None:
        self.stack = stack
        self.config_cls = config_cls
        self.fields: list[FieldSpec] = []
        self.steps: list[ConfigWorkflowStep] = []
        self.bindings: dict[str, _Binding] = {}

    def build(self) -> _PluginForm:
        stored = self.stack.tools.get(self.config_cls.tool_name, {})
        self._add_model(
            self.config_cls,
            path=(),
            conditions=(),
            stored=stored,
            ancestry=(),
        )
        if not self.steps:
            raise UnsupportedPluginConfigSchema(
                f"{self.config_cls.__name__} has no fields the generic wizard can edit."
            )
        return _PluginForm(tuple(self.fields), tuple(self.steps), dict(self.bindings))

    def _add_model(
        self,
        model_cls: type[BaseModel],
        *,
        path: tuple[str, ...],
        conditions: tuple[ConfigBranchCondition, ...],
        stored: Mapping[str, object],
        ancestry: tuple[type[BaseModel], ...],
    ) -> None:
        if model_cls in ancestry:
            raise UnsupportedPluginConfigSchema(
                f"{model_cls.__name__} contains a recursive RefTo cycle."
            )
        scalar_keys: list[str] = []
        for name, info in model_cls.model_fields.items():
            ref = _ref_marker(info)
            if ref is not None:
                self._add_reference(
                    name,
                    info,
                    ref,
                    path=path,
                    conditions=conditions,
                    stored=stored.get(name),
                    ancestry=(*ancestry, model_cls),
                )
                continue
            spec = _scalar_field_spec(name, info, stored.get(name))
            if spec is None:
                continue
            key = _key((*path, name))
            spec = _replace_key(spec, key)
            self.fields.append(spec)
            self.bindings[key] = _Binding(key, (*path, name), "scalar")
            scalar_keys.append(key)
        if scalar_keys:
            step_path = path or ("settings",)
            self.steps.append(
                ConfigWorkflowStep(
                    _step_key(step_path, "fields"),
                    _title(path[-1] if path else "Plugin settings"),
                    tuple(scalar_keys),
                    when=conditions,
                )
            )

    def _add_reference(
        self,
        name: str,
        info: Any,
        ref: RefTo,
        *,
        path: tuple[str, ...],
        conditions: tuple[ConfigBranchCondition, ...],
        stored: object,
        ancestry: tuple[type[BaseModel], ...],
    ) -> None:
        section_name = _REFERENCE_SECTIONS.get(ref.target)
        if section_name is None:
            raise UnsupportedPluginConfigSchema(
                f"{self.config_cls.__name__}.{name} targets unsupported {ref.target.__name__}."
            )
        section = getattr(self.stack, section_name)
        existing = sorted(
            entry_name
            for entry_name, entry in section.items()
            if isinstance(entry, ref.target)
        )
        required = info.is_required() and info.default is PydanticUndefined
        choices = [ChoiceOption(value, value) for value in existing]
        if not required:
            choices.insert(0, ChoiceOption(_NONE, "Not configured"))
        choices.append(ChoiceOption(_CREATE, f"Create new {section_name[:-1]}"))
        default = (
            str(stored)
            if stored in existing
            else str(info.default)
            if info.default not in (None, PydanticUndefined) and str(info.default) in existing
            else existing[0]
            if existing
            else _CREATE
            if required
            else _NONE
        )
        reference_path = (*path, name)
        reference_key = _key(reference_path)
        self.fields.append(
            FieldSpec(
                reference_key,
                _title(name),
                kind=FieldKind.CHOICE,
                choices=tuple(choices),
                default=default,
                help=info.description,
            )
        )
        self.bindings[reference_key] = _Binding(
            reference_key, reference_path, "reference"
        )
        self.steps.append(
            ConfigWorkflowStep(
                reference_key,
                _title(name),
                (reference_key,),
                kind=ConfigWorkflowStepKind.CHOICE,
                when=conditions,
            )
        )
        create_conditions = (
            *conditions,
            ConfigBranchCondition(reference_key, frozenset({_CREATE})),
        )
        name_path = (*reference_path, "name")
        name_key = _key(name_path)
        self.fields.append(
            FieldSpec(
                name_key,
                f"New {_title(section_name[:-1])} name",
                default=name,
            )
        )
        self.bindings[name_key] = _Binding(
            name_key,
            name_path,
            "name",
            section=section_name,
        )
        self.steps.append(
            ConfigWorkflowStep(
                _step_key(reference_path, "name"),
                f"New {_title(section_name[:-1])}",
                (name_key,),
                when=create_conditions,
            )
        )
        self._add_model(
            ref.target,
            path=reference_path,
            conditions=create_conditions,
            stored={},
            ancestry=ancestry,
        )


def _ref_marker(info: Any) -> RefTo | None:
    return next((item for item in info.metadata if isinstance(item, RefTo)), None)


def _scalar_field_spec(name: str, info: Any, stored: object) -> FieldSpec | None:
    annotation, optional = _without_none(info.annotation)
    required = info.is_required() and not optional
    default = (
        stored
        if stored is not None and not is_sensitive(info)
        else info.default
        if info.default is not PydanticUndefined and not is_sensitive(info)
        else None
    )
    if is_sensitive(info):
        return FieldSpec(
            key=name,
            label=_title(name),
            kind=FieldKind.SECRET,
            required=required,
            default=default,
            help=info.description,
            sensitive=True,
            secret_clearable=False,
        )
    if annotation is str:
        return FieldSpec(
            key=name,
            label=_title(name),
            required=required,
            default=default,
            help=info.description,
        )
    if annotation is bool:
        return FieldSpec(
            key=name,
            label=_title(name),
            kind=FieldKind.BOOLEAN,
            required=required,
            default=default,
            help=info.description,
        )
    if annotation is int:
        minimum, maximum, validator = _numeric_constraints(info, integer=True)
        return FieldSpec(
            key=name,
            label=_title(name),
            kind=FieldKind.INTEGER,
            required=required,
            default=default,
            help=info.description,
            minimum=minimum,
            maximum=maximum,
            validator=validator,
        )
    if annotation is float:
        minimum, maximum, validator = _numeric_constraints(info, integer=False)
        return FieldSpec(
            key=name,
            label=_title(name),
            kind=FieldKind.DECIMAL,
            required=required,
            default=default,
            help=info.description,
            minimum=minimum,
            maximum=maximum,
            validator=validator,
        )
    if annotation is Path:
        raise UnsupportedPluginConfigSchema(
            f"Path field {name!r} does not declare whether it is an input or output path; "
            "provide a custom plugin workflow."
        )
    origin = get_origin(annotation)
    if origin is Literal:
        values = get_args(annotation)
        if len(values) == 1:
            return None
        return FieldSpec(
            key=name,
            label=_title(name),
            kind=FieldKind.CHOICE,
            required=required,
            default=default,
            help=info.description,
            choices=tuple(ChoiceOption(str(value), str(value)) for value in values),
        )
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return FieldSpec(
            key=name,
            label=_title(name),
            kind=FieldKind.CHOICE,
            required=required,
            default=default,
            help=info.description,
            choices=tuple(
                ChoiceOption(str(member.value), member.name.replace("_", " ").title())
                for member in annotation
            ),
        )
    if not required:
        return None
    raise UnsupportedPluginConfigSchema(
        f"Required field {name!r} has unsupported type {annotation!r}; provide a custom plugin workflow."
    )


def _numeric_constraints(info: Any, *, integer: bool):
    minimum: int | Decimal | None = None
    maximum: int | Decimal | None = None
    exclusive_minimum = None
    exclusive_maximum = None
    for marker in info.metadata:
        if getattr(marker, "ge", None) is not None:
            minimum = marker.ge
        if getattr(marker, "le", None) is not None:
            maximum = marker.le
        if getattr(marker, "gt", None) is not None:
            if integer:
                minimum = int(marker.gt) + 1
            else:
                exclusive_minimum = Decimal(str(marker.gt))
        if getattr(marker, "lt", None) is not None:
            if integer:
                maximum = int(marker.lt) - 1
            else:
                exclusive_maximum = Decimal(str(marker.lt))

    def validate(value: object) -> ValidationIssue | None:
        decimal = Decimal(str(value))
        if exclusive_minimum is not None and decimal <= exclusive_minimum:
            return ValidationIssue(f"Must be greater than {exclusive_minimum}.")
        if exclusive_maximum is not None and decimal >= exclusive_maximum:
            return ValidationIssue(f"Must be less than {exclusive_maximum}.")
        return None

    validator = validate if exclusive_minimum is not None or exclusive_maximum is not None else None
    return minimum, maximum, validator


def _without_none(annotation: object) -> tuple[object, bool]:
    origin = get_origin(annotation)
    if origin in (types.UnionType,):
        args = get_args(annotation)
        if type(None) in args and len(args) == 2:
            return next(arg for arg in args if arg is not type(None)), True
    return annotation, False


def _nested_set_dict(
    answers: Mapping[str, object], bindings: Mapping[str, _Binding]
) -> dict[str, object]:
    result: dict[str, object] = {}
    ordered = sorted(
        (binding for key, binding in bindings.items() if key in answers),
        key=lambda binding: (len(binding.path), binding.kind != "reference"),
    )
    for binding in ordered:
        value = answers[binding.key]
        if binding.kind == "reference" and value == _NONE:
            _set_nested(result, binding.path, None)
            continue
        if binding.kind == "reference" and value == _CREATE:
            _set_nested(result, binding.path, {})
            continue
        _set_nested(result, binding.path, value)
    return result


def _new_name_issues(
    stack: StackConfig,
    answers: Mapping[str, object],
    bindings: Mapping[str, _Binding],
) -> tuple[ValidationIssue, ...]:
    issues = []
    for key, binding in bindings.items():
        if binding.kind != "name" or binding.section is None or key not in answers:
            continue
        name = str(answers[key])
        if name in getattr(stack, binding.section):
            issues.append(
                ValidationIssue(
                    f"{name!r} already exists in {binding.section}; choose the existing "
                    "reference or enter a new name.",
                    field_key=key,
                )
            )
    return tuple(issues)


def _set_nested(target: dict[str, object], path: tuple[str, ...], value: object) -> None:
    current = target
    for part in path[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            return
        current = child
    current[path[-1]] = value


def _flatten_stack(stack: StackConfig) -> dict[str, object]:
    flattened: dict[str, object] = {}

    def visit(prefix: str, value: object) -> None:
        if isinstance(value, Mapping):
            for key in sorted(value, key=str):
                visit(f"{prefix}.{key}" if prefix else str(key), value[key])
            return
        if isinstance(value, list | tuple):
            for index, item in enumerate(value):
                visit(f"{prefix}.{index}", item)
            return
        flattened[prefix] = value

    visit("", stack.model_dump(mode="python", exclude_none=True))
    return flattened


def _sensitive_paths(model: BaseModel, prefix: str = "") -> set[str]:
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


def _replace_key(spec: FieldSpec, key: str) -> FieldSpec:
    return FieldSpec(
        key=key,
        label=spec.label,
        kind=spec.kind,
        required=spec.required,
        default=spec.default,
        help=spec.help,
        placeholder=spec.placeholder,
        choices=spec.choices,
        minimum=spec.minimum,
        maximum=spec.maximum,
        disabled=spec.disabled,
        read_only=spec.read_only,
        sensitive=spec.sensitive,
        secret_clearable=spec.secret_clearable,
        validator=spec.validator,
        select_on_focus=spec.select_on_focus,
    )


def _key(path: tuple[str, ...]) -> str:
    return _SEPARATOR.join(path)


def _step_key(path: tuple[str, ...], suffix: str) -> str:
    return "plugin-" + "-".join((*path, suffix)).replace("_", "-")


def _title(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


__all__ = [
    "PackageConfigMutationService",
    "UnsupportedPluginConfigSchema",
    "plugin_config_target",
]
