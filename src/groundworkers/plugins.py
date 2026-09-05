"""Contract for external capability plugins.

A plugin is a separate installable package — its own adapter(s), service, and
MCP tools — that registers itself under the ``groundworkers.plugins``
entry-point group and implements :class:`GroundworkersPlugin`. Adding or
removing a plugin package changes nothing in this repository: `app.py` and
`server.py` only depend on the contract in this module, never on any
particular plugin.

See ``docs/development/plugins.md`` for the full design rationale. This
module is the runtime contract that document describes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from groundskeeping.contracts import CommandPlan
from oa_configurator import (  # type: ignore[import-untyped]
    PackageConfigBase,
    ResolvedDatabase,
    ResolvedModel,
    ResolvedVectorStore,
    Resolver,
)
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from groundskeeping.configurator import (
        ConfigMutationService,
        ConfigWorkflowSpec,
        MutationOperation,
    )

logger = logging.getLogger(__name__)

_PLUGIN_SETUP_FIELD_KINDS = frozenset(
    {
        "text",
        "integer",
        "decimal",
        "boolean",
        "choice",
        "existing_path",
        "output_path",
        "secret",
        "multiline",
    }
)


class PluginReadinessState(StrEnum):
    """Stable, presentation-independent readiness states for plugins."""

    UNCONFIGURED = "unconfigured"
    WARNING = "warning"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class PluginReadinessField:
    """One explicitly safe field in a plugin readiness report.

    Plugins must only put operator-safe display text here. In particular, values
    must not contain connection URLs, credentials, or raw exception messages.
    """

    key: str
    label: str
    value: str
    state: PluginReadinessState
    detail: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """Return the stable transport shape used by status tools."""

        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "state": self.state.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PluginReadinessResult:
    """Read-only plugin verification result shared by headless and UI hosts."""

    state: PluginReadinessState
    summary: str
    fields: tuple[PluginReadinessField, ...] = ()
    configured: bool = True

    @property
    def ready(self) -> bool:
        return self.state is PluginReadinessState.READY

    def as_dict(self) -> dict[str, object]:
        """Return a stable, JSON-compatible readiness response."""

        return {
            "state": self.state.value,
            "configured": self.configured,
            "ready": self.ready,
            "summary": self.summary,
            "fields": [field.as_dict() for field in self.fields],
        }


@dataclass(frozen=True)
class PluginSetupArgument:
    """One argument rendered by Groundworkers' generic setup wizard.

    ``kind`` uses the string values of Groundskeeping's ``FieldKind`` without
    making the MCP/runtime plugin contract depend on the optional TUI package.
    """

    key: str
    label: str
    kind: str = "text"
    required: bool = True
    default: object | None = None
    help: str | None = None


@dataclass(frozen=True)
class PluginSetupStep:
    """Declarative, background-capable setup operation owned by a plugin.

    The host owns argument presentation, review, persistence, logs, retry, and
    cancellation. The plugin only translates validated values into a durable
    ``CommandPlan``; it does not get a second CLI or TUI framework.
    """

    key: str
    title: str
    purpose: str
    arguments: tuple[PluginSetupArgument, ...]
    build_plan: Callable[[Mapping[str, object], str], CommandPlan]
    apply_label: str = "Run"


@dataclass(frozen=True)
class PluginConfigResolver:
    """
    Wraps `oa_configurator.Resolver` so a plugin's dependency
    on oa-configurator can't grow past what core itself relies
    on today.
    """

    _resolver: Resolver

    def resolve_database(self, name: str) -> ResolvedDatabase:
        return self._resolver.resolve_database(name)

    def resolve_model(self, name: str) -> ResolvedModel:
        return self._resolver.resolve_model(name)

    def resolve_vector_store(self, name: str) -> ResolvedVectorStore:
        return self._resolver.resolve_vector_store(name)


@dataclass(frozen=True)
class PluginContext:
    """Resolved, read-only handles passed to every plugin.

    Not `AppConfig`, and — deliberately — not `StackConfig` either: this
    shape is the stability boundary plugins are written against, and must
    keep changing independently of both.

    Most plugins want the CDM database and/or the embedding vector store;
    those come already resolved (`cdm_database`/`cdm_engine`,
    `vector_store`/`embedding_backend_factory`) so a plugin that only needs
    those needs no config of its own at all. A plugin that genuinely needs an
    independent database, model, or vector store resolves it itself via
    `resolver`.
    """

    resolver: PluginConfigResolver
    cdm_database: ResolvedDatabase | None
    cdm_engine: Engine | None
    vector_store: ResolvedVectorStore | None
    embedding_backend_factory: Callable[[], Any] | None
    embedding_model_backend_factory: Callable[[], Any] | None
    chat_backend_factory: Callable[[], Any] | None


class GroundworkersPlugin(Protocol):
    """What a plugin package implements.

    `ep.load()` (see :func:`discover_plugins`) should resolve to a
    ready-to-use instance of this protocol — a module-level singleton is the
    simplest shape — not a class needing construction.
    """

    name: str
    config_cls: ClassVar[type[PackageConfigBase] | None]

    def build(
        self, context: PluginContext, config: PackageConfigBase | None
    ) -> object | None:
        """Construct this plugin's adapter(s) + service.

        `config` is this plugin's own validated config, resolved even when
        `[tools.<name>]` is entirely absent (fields fall back to their own
        defaults, the same way `GroundworkersConfig` resolves fine with no
        `[tools.groundworkers]` section at all). It is `None` only when
        `config_cls` is `None`, or when an explicitly-set field failed
        validation and the host treated that the same as an unavailable
        optional backend. Return `None` when a required prerequisite is
        absent; `register` is never called in that case.
        """
        ...

    def register(self, server: Any, state: object) -> None:
        """Register this plugin's MCP tools/resources against `state`.

        No `try/except` needed here: `GroundworkersMCPServer.tool()` already
        catches `GroundworkersError`, converts a bare `ValueError` to
        `INVALID_INPUT`, and converts anything else to a logged
        `INTERNAL_ERROR` — the same guard every core tool gets.
        """
        ...


@runtime_checkable
class GroundworkersPluginConfigUI(Protocol):
    """Optional custom TUI workflow for plugins that exceed generic Tier A."""

    def tui_workflow(
        self, operation: MutationOperation
    ) -> tuple[ConfigWorkflowSpec, ConfigMutationService] | None:
        """Return a custom workflow/provider pair, or defer to generic Tier A."""
        ...


@runtime_checkable
class GroundworkersPluginSetup(Protocol):
    """Optional setup operations exposed in the host setup console."""

    setup_steps: ClassVar[tuple[PluginSetupStep, ...]]


@runtime_checkable
class GroundworkersPluginReadiness(Protocol):
    """Optional read-only verification implemented by a plugin.

    The plugin receives the same state it built for MCP registration and
    returns presentation-independent, explicitly safe display data. This keeps
    the runtime contract free of Groundskeeping and TUI types.
    """

    def verify_readiness(self, state: object) -> PluginReadinessResult:
        """Inspect current dependencies without mutating them."""

        ...


def discover_plugins() -> list[GroundworkersPlugin]:
    """Discover installed plugins via the `groundworkers.plugins` entry-point group.

    Mirrors how `oa-configurator` discovers package config classes under its
    own `omop.config` group — same mechanism, new group name. Called once
    while building the runtime (`build_application`) and once while
    registering tools (`create_server`); cheap enough that caching isn't
    worth the added state.
    """

    discovered: list[GroundworkersPlugin] = []
    for entry_point in entry_points(group="groundworkers.plugins"):
        try:
            discovered.append(entry_point.load())
        except Exception:
            logger.exception(
                "Could not load Groundworkers plugin entry point %s; skipping it.",
                entry_point.name,
            )
    validate_plugin_identities(discovered)
    return discovered


def validate_plugin_identities(plugins: list[GroundworkersPlugin]) -> None:
    """Reject ambiguous runtime/config identities before any plugin is built."""

    seen: set[str] = set()
    setup_seen: set[str] = set()
    for plugin in plugins:
        if not plugin.name or plugin.name in seen:
            raise ValueError(
                f"Groundworkers plugin name {plugin.name!r} is not unique."
            )
        seen.add(plugin.name)
        if plugin.config_cls is not None and plugin.config_cls.tool_name != plugin.name:
            raise ValueError(
                f"Groundworkers plugin {plugin.name!r} uses configuration identity "
                f"{plugin.config_cls.tool_name!r}; the names must match."
            )
        for step in getattr(plugin, "setup_steps", ()):
            if not step.key or step.key in setup_seen:
                raise ValueError(
                    f"Groundworkers setup step {step.key!r} is not unique."
                )
            if not step.key.startswith(f"{plugin.name}_"):
                raise ValueError(
                    f"Groundworkers setup step {step.key!r} must start with "
                    f"the plugin name {plugin.name!r} followed by an underscore."
                )
            argument_keys = [argument.key for argument in step.arguments]
            if len(argument_keys) != len(set(argument_keys)):
                raise ValueError(
                    f"Groundworkers setup step {step.key!r} has duplicate arguments."
                )
            if any(not argument.key for argument in step.arguments):
                raise ValueError(
                    f"Groundworkers setup step {step.key!r} has an argument without a key."
                )
            unsupported_kinds = sorted(
                {
                    argument.kind
                    for argument in step.arguments
                    if argument.kind not in _PLUGIN_SETUP_FIELD_KINDS
                }
            )
            if unsupported_kinds:
                raise ValueError(
                    f"Groundworkers setup step {step.key!r} uses unsupported "
                    f"argument kinds: {unsupported_kinds}."
                )
            setup_seen.add(step.key)
