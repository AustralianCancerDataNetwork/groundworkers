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
from typing import ClassVar, cast

import pytest
from oa_configurator import PackageConfigBase
from pydantic import Field

from groundworkers.app import build_application, verify_plugin_readiness
from groundworkers.base.errors import GroundworkersError
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.plugins import (
    GroundworkersPluginLinter,
    PluginContext,
    PluginLintDiagnostic,
    PluginLintReport,
    PluginLintSeverity,
    PluginReadinessField,
    PluginReadinessResult,
    PluginReadinessState,
    discover_plugins,
    lint_plugin_config,
    validate_plugin_identities,
)
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

    def build(self, context: PluginContext, config: PackageConfigBase | None):
        if not isinstance(config, FakePluginConfig) or context.vector_store is None:
            return None
        return {"greeting": config.greeting, "vector_store": context.vector_store}

    def register(self, server, state) -> None:
        @server.tool("fake_plugin_greet")
        def fake_plugin_greet() -> dict:
            return {"greeting": state["greeting"]}

        @server.tool("fake_plugin_fail")
        def fake_plugin_fail() -> dict:
            raise GroundworkersError("NOT_FOUND", "nothing here")


class FakeReadinessPlugin(FakePlugin):
    def verify_readiness(self, state: object) -> PluginReadinessResult:
        assert isinstance(state, dict)
        return PluginReadinessResult(
            state=PluginReadinessState.READY,
            summary="Fake plugin is ready.",
            fields=(
                PluginReadinessField(
                    "vector_store",
                    "Vector store",
                    "Available",
                    PluginReadinessState.READY,
                    "The shared vector store is available.",
                ),
            ),
        )


class FakeLinterPlugin(FakePlugin):
    def lint_config(self, config: PackageConfigBase | None) -> PluginLintReport:
        assert isinstance(config, FakePluginConfig)
        return PluginLintReport(
            plugin=self.name,
            diagnostics=(
                PluginLintDiagnostic(
                    "fake.setting.warning",
                    PluginLintSeverity.WARNING,
                    "The setting is unusual.",
                    "tools.fake_plugin.greeting",
                ),
            ),
        )


class FailingLinterPlugin(FakeLinterPlugin):
    def lint_config(self, config: PackageConfigBase | None) -> PluginLintReport:
        raise RuntimeError("secret-token-must-not-be-logged")


class InvalidReportLinterPlugin(FakeLinterPlugin):
    def lint_config(self, config: PackageConfigBase | None) -> PluginLintReport:
        return PluginLintReport(plugin="another_plugin")


class MalformedReportLinterPlugin(FakeLinterPlugin):
    def lint_config(self, config: PackageConfigBase | None) -> PluginLintReport:
        return PluginLintReport(plugin=self.name, diagnostics=("invalid",))


def _embedding_stack(tmp_path: Path, extra: dict | None = None):
    stack = build_embedding_stack()
    # Redirect the sqlite-backed vector store off the real filesystem/cwd,
    # matching tests/test_server_registry.py's own embedding-stack fixtures.
    stack.connections["embedding_main"].database_name = str(tmp_path / "emb.db")
    if extra is not None:
        stack.tools["fake_plugin"] = dict(extra)
    return stack


def test_discover_plugins_returns_empty_controlled_registry(monkeypatch) -> None:
    """A core-only host has no plugins when the registry boundary is empty."""
    monkeypatch.setattr("groundworkers.plugins.entry_points", lambda **_kwargs: ())
    assert discover_plugins() == []


class FakeEntryPoint:
    def __init__(self, name: str, plugin=None, error: Exception | None = None) -> None:
        self.name = name
        self.plugin = plugin
        self.error = error

    def load(self):
        if self.error is not None:
            raise self.error
        return self.plugin


def test_discover_plugins_loads_one_and_multiple_plugins(monkeypatch) -> None:
    first, second = FakePlugin(), FakeReadinessPlugin()
    second.name = "readiness_plugin"
    second.config_cls = None
    registry = [FakeEntryPoint("first", first)]
    monkeypatch.setattr("groundworkers.plugins.entry_points", lambda **_kwargs: registry)
    assert discover_plugins() == [first]

    registry.append(FakeEntryPoint("second", second))
    assert discover_plugins() == [first, second]


def test_broken_plugin_entry_point_does_not_hide_a_valid_plugin(monkeypatch) -> None:
    valid = FakePlugin()
    monkeypatch.setattr(
        "groundworkers.plugins.entry_points",
        lambda **_kwargs: (
            FakeEntryPoint("broken", error=ImportError("plugin import failed")),
            FakeEntryPoint("valid", valid),
        ),
    )

    assert discover_plugins() == [valid]


def test_discovery_rejects_duplicate_plugin_identities(monkeypatch) -> None:
    monkeypatch.setattr(
        "groundworkers.plugins.entry_points",
        lambda **_kwargs: (FakeEntryPoint("one", FakePlugin()), FakeEntryPoint("two", FakePlugin())),
    )
    with pytest.raises(ValueError, match="not unique"):
        discover_plugins()


def test_build_application_uses_config_defaults_when_no_section_present(
    tmp_path: Path, monkeypatch
) -> None:
    """A package's own fields may all have usable defaults, so an absent
    [tools.fake_plugin] section is not itself a reason to skip -- same as
    GroundworkersConfig resolving fine with no [tools.groundworkers] at all."""
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(_embedding_stack(tmp_path))  # no section
    app = build_application(config)
    state = cast(dict, app.plugins["fake_plugin"])
    assert state["greeting"] == "hello"


def test_build_application_honors_an_explicit_config_value(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(
        _embedding_stack(tmp_path, {"greeting": "hi there"})
    )
    app = build_application(config)
    state = cast(dict, app.plugins["fake_plugin"])
    assert state["greeting"] == "hi there"
    # Default path: reuses the core vector store, no second one is resolved.
    assert state["vector_store"] is config.vector_store


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
    assert app.plugin_issues["fake_plugin"].startswith("invalid configuration")


def test_build_application_skips_plugin_when_prerequisite_backend_is_absent(
    monkeypatch,
) -> None:
    """A CDM-only stack has no vector store; build() opts out on its own
    terms, the same way a core service does when its adapter is unavailable."""
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(build_cdm_stack())
    app = build_application(config)
    assert "fake_plugin" not in app.plugins
    assert app.plugin_issues == {
        "fake_plugin": "plugin prerequisites are unavailable"
    }


def test_create_server_registers_plugin_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
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
    config = build_app_config_from_stack(build_cdm_stack())
    server = create_server(config)
    assert "fake_plugin_greet" not in server.list_tools()


def test_plugin_tool_errors_come_back_in_the_shared_shape(
    tmp_path: Path, monkeypatch
) -> None:
    """A plugin's GroundworkersError survives the shared server.tool() guard
    unmodified, without the plugin writing its own try/except."""
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(_embedding_stack(tmp_path))
    server = create_server(config)
    result = server.call("fake_plugin_fail")
    assert result == {"error": True, "code": "NOT_FOUND", "message": "nothing here"}


def test_describe_reports_loaded_plugin_names(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groundworkers.app.discover_plugins", lambda: [FakePlugin()])
    config = build_app_config_from_stack(_embedding_stack(tmp_path))
    app = build_application(config)
    assert sorted(app.plugins) == ["fake_plugin"]


def test_plugin_identities_must_be_unique_and_match_their_config() -> None:
    with pytest.raises(ValueError, match="not unique"):
        validate_plugin_identities([FakePlugin(), FakePlugin()])

    mismatched = FakePlugin()
    mismatched.name = "different_name"
    with pytest.raises(ValueError, match="names must match"):
        validate_plugin_identities([mismatched])


def test_optional_readiness_contract_is_headless_and_transport_stable(
    tmp_path: Path,
) -> None:
    config = build_app_config_from_stack(_embedding_stack(tmp_path))

    report = verify_plugin_readiness(config, FakeReadinessPlugin())

    assert report.as_dict() == {
        "state": "ready",
        "configured": True,
        "ready": True,
        "summary": "Fake plugin is ready.",
        "fields": [
            {
                "key": "vector_store",
                "label": "Vector store",
                "value": "Available",
                "state": "ready",
                "detail": "The shared vector store is available.",
            }
        ],
    }
    assert PluginReadinessResult.__module__ == "groundworkers.plugins"


def test_optional_plugin_linter_is_discovered_and_returns_stable_report() -> None:
    plugin = FakeLinterPlugin()

    report = lint_plugin_config(plugin, FakePluginConfig())

    assert isinstance(plugin, GroundworkersPluginLinter)
    assert report is not None
    assert report.as_dict() == {
        "plugin": "fake_plugin",
        "valid": True,
        "diagnostics": [
            {
                "code": "fake.setting.warning",
                "severity": "warning",
                "message": "The setting is unusual.",
                "path": "tools.fake_plugin.greeting",
            }
        ],
    }


def test_linter_dispatch_skips_plugins_without_the_optional_protocol() -> None:
    assert lint_plugin_config(FakePlugin(), FakePluginConfig()) is None


@pytest.mark.parametrize(
    ("plugin", "code"),
    [
        (FailingLinterPlugin(), "plugin_linter.failed"),
        (InvalidReportLinterPlugin(), "plugin_linter.invalid_report"),
        (MalformedReportLinterPlugin(), "plugin_linter.invalid_report"),
    ],
)
def test_linter_dispatch_returns_safe_error_for_broken_plugin(
    plugin: FakePlugin, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    report = lint_plugin_config(plugin, FakePluginConfig())

    assert report is not None
    assert not report.valid
    assert report.diagnostics[0].code == code
    assert "secret-token" not in report.diagnostics[0].message
    assert "secret-token" not in caplog.text


def test_readiness_reports_unconfigured_when_plugin_cannot_build() -> None:
    report = verify_plugin_readiness(
        build_app_config_from_stack(build_cdm_stack()),
        FakeReadinessPlugin(),
    )

    assert report.state is PluginReadinessState.UNCONFIGURED
    assert not report.configured
    assert not report.ready
    assert "prerequisites" in report.summary
