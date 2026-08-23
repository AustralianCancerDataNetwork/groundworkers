"""Contract tests for the plugin loader.

Exercises the touchpoints described in docs/development/plugins.md using one
fake in-process plugin, monkeypatched in place of real entry-point discovery
-- a plugin author writes the same shape of test against their own plugin,
without needing a real installed entry point either.

FakePlugin requires the embedding vector store (a real, if optional,
prerequisite) rather than the CDM engine, because `AppConfig.cdm_engine` is
never `None` once a stack resolves at all -- there is no way to reach a
"prerequisite absent" state through it. The vector store is genuinely
optional, so it exercises the same branch a real plugin's "needs embeddings"
check would.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from oa_configurator import PackageConfigBase
from pydantic import Field

from groundworkers.app import build_application
from groundworkers.base.errors import GroundworkersError
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.plugins import PluginContext, discover_plugins
from groundworkers.server import create_server
from tests.support.stack_config import build_cdm_stack, build_embedding_stack


class FakePluginConfig(PackageConfigBase):
    tool_name: ClassVar[str] = "fake_plugin"

    greeting: str = Field(default="hello")
    retries: int = Field(default=1, ge=0)


class FakePlugin:
    """A minimal plugin: no adapter of its own, reuses the core vector store."""

    name = "fake_plugin"
    config_cls = FakePluginConfig

    def build(self, context: PluginContext, config: FakePluginConfig | None):
        if config is None or context.vector_store is None:
            return None
        return {"greeting": config.greeting, "vector_store": context.vector_store}

    def register(self, server, state) -> None:
        @server.tool("fake_plugin_greet")
        def fake_plugin_greet() -> dict:
            return {"greeting": state["greeting"]}

        @server.tool("fake_plugin_fail")
        def fake_plugin_fail() -> dict:
            raise GroundworkersError("NOT_FOUND", "nothing here")


def _embedding_stack(tmp_path: Path, extra: dict | None = None):
    stack = build_embedding_stack()
    # Redirect the sqlite-backed vector store off the real filesystem/cwd,
    # matching tests/test_server_registry.py's own embedding-stack fixtures.
    stack.connections["embedding_main"].database_name = str(tmp_path / "emb.db")
    if extra is not None:
        stack.tools["fake_plugin"] = dict(extra)
    return stack


def test_discover_plugins_finds_nothing_by_default() -> None:
    """No entry points are registered in this environment; the loader is inert."""
    assert discover_plugins() == []


def test_build_application_uses_config_defaults_when_no_section_present(
    tmp_path: Path, monkeypatch
) -> None:
    """A package's own fields may all have usable defaults, so an absent
    [tools.fake_plugin] section is not itself a reason to skip -- same as
    GroundworkersConfig resolving fine with no [tools.groundworkers] at all."""
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(_embedding_stack(tmp_path))  # no section
    app = build_application(config)
    assert app.plugins["fake_plugin"]["greeting"] == "hello"


def test_build_application_honors_an_explicit_config_value(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(
        _embedding_stack(tmp_path, {"greeting": "hi there"})
    )
    app = build_application(config)
    assert app.plugins["fake_plugin"]["greeting"] == "hi there"
    # Default path: reuses the core vector store, no second one is resolved.
    assert app.plugins["fake_plugin"]["vector_store"] is config.vector_store


def test_build_application_skips_a_plugin_with_an_invalid_config_value(
    tmp_path: Path, monkeypatch
) -> None:
    """A field failing its own pydantic constraint is a validation error,
    which PackageConfigBase.validate_candidate raises as ConfigurationError --
    confirm the host treats that the same as "not configured" (skip, log,
    keep starting up) rather than a fatal error."""
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(_embedding_stack(tmp_path, {"retries": -1}))
    app = build_application(config)
    assert "fake_plugin" not in app.plugins


def test_build_application_skips_plugin_when_prerequisite_backend_is_absent(
    monkeypatch,
) -> None:
    """A CDM-only stack has no vector store; build() opts out on its own
    terms, the same way a core service does when its adapter is unavailable."""
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(build_cdm_stack())
    app = build_application(config)
    assert "fake_plugin" not in app.plugins


def test_create_server_registers_plugin_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    monkeypatch.setattr("groundworkers.server.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(
        _embedding_stack(tmp_path, {"greeting": "hi"})
    )
    server = create_server(config)
    assert "fake_plugin_greet" in server.list_tools()
    assert server.call("fake_plugin_greet") == {"greeting": "hi"}


def test_create_server_skips_registration_when_build_returns_none(
    monkeypatch,
) -> None:
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    monkeypatch.setattr("groundworkers.server.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(build_cdm_stack())
    server = create_server(config)
    assert "fake_plugin_greet" not in server.list_tools()


def test_plugin_tool_errors_come_back_in_the_shared_shape(
    tmp_path: Path, monkeypatch
) -> None:
    """A plugin's GroundworkersError survives the shared server.tool() guard
    unmodified, without the plugin writing its own try/except."""
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    monkeypatch.setattr("groundworkers.server.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(_embedding_stack(tmp_path))
    server = create_server(config)
    result = server.call("fake_plugin_fail")
    assert result == {"error": True, "code": "NOT_FOUND", "message": "nothing here"}


def test_describe_reports_loaded_plugin_names(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(_embedding_stack(tmp_path))
    app = build_application(config)
    assert sorted(app.plugins) == ["fake_plugin"]
