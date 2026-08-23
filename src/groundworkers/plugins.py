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
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

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
    for plugin in plugins:
        if not plugin.name or plugin.name in seen:
            raise ValueError(f"Groundworkers plugin name {plugin.name!r} is not unique.")
        seen.add(plugin.name)
        if (
            plugin.config_cls is not None
            and plugin.config_cls.tool_name != plugin.name
        ):
            raise ValueError(
                f"Groundworkers plugin {plugin.name!r} uses configuration identity "
                f"{plugin.config_cls.tool_name!r}; the names must match."
            )
