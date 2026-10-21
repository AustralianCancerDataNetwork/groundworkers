from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from oa_configurator import ConfigurationError, PackageConfigBase

from groundworkers.plugins import (
    PluginLintDiagnostic,
    PluginLintReport,
    PluginLintSeverity,
)
from groundworkers.server import main, parse_args


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--config-path`` exports OA_CONFIG_PATH; restore it after each test.

    ``delenv`` alone records nothing when the variable is unset, so ``setenv``
    first makes monkeypatch restore the original state on teardown.
    """

    monkeypatch.setenv("OA_CONFIG_PATH", "")
    monkeypatch.delenv("OA_CONFIG_PATH")


class LinterPlugin:
    name = "lintable"
    config_cls = None

    def lint_config(self, config: PackageConfigBase | None) -> PluginLintReport:
        assert config is None
        return PluginLintReport(
            plugin=self.name,
            diagnostics=(
                PluginLintDiagnostic(
                    "example.warning",
                    PluginLintSeverity.WARNING,
                    "An example warning.",
                    "tools.lintable.setting",
                ),
            ),
        )


class NonLinterPlugin:
    name = "legacy"


class InvalidPluginConfig:
    @classmethod
    def validate_candidate(cls, stack: object) -> PackageConfigBase:
        raise ConfigurationError("secret-value-must-not-be-printed")


class InvalidConfigLinter(LinterPlugin):
    name = "invalid_config"
    config_cls = InvalidPluginConfig


class RelativeCatalogueLinter(LinterPlugin):
    name = "relative_catalogue"

    def lint_config(self, config: PackageConfigBase | None) -> PluginLintReport:
        assert config is None
        selected_config = Path(os.environ["OA_CONFIG_PATH"])
        assert (selected_config.parent / "catalogue.json").is_file()
        return PluginLintReport(plugin=self.name)


def test_parse_args_accepts_plugin_lint_command() -> None:
    args = parse_args(["lint-plugins", "--config-path", "/tmp/stack.toml"])

    assert args.command == "lint-plugins"
    assert args.config_path == "/tmp/stack.toml"


def test_lint_plugins_reports_results_without_building_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("groundworkers.server.load_stack_config", lambda _path: object())
    monkeypatch.setattr(
        "groundworkers.server.build_app_config",
        lambda **_kwargs: pytest.fail("linting must not build runtime services"),
    )
    monkeypatch.setattr(
        "groundworkers.plugins.discover_plugins",
        lambda: [LinterPlugin(), NonLinterPlugin()],
    )

    main(["lint-plugins", "--config-path", "/tmp/stack.toml"])

    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True
    assert result["checked"] == 1
    assert result["skipped"] == ["legacy"]
    assert result["reports"][0]["plugin"] == "lintable"


def test_lint_plugins_reports_invalid_config_without_leaking_error_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("groundworkers.server.load_stack_config", lambda _path: object())
    monkeypatch.setattr(
        "groundworkers.plugins.discover_plugins", lambda: [InvalidConfigLinter()]
    )

    with pytest.raises(SystemExit) as exit_info:
        main(["lint-plugins", "--config-path", "/tmp/stack.toml"])

    output = capsys.readouterr().out
    result = json.loads(output)
    assert exit_info.value.code == 1
    assert result["valid"] is False
    assert result["reports"][0]["diagnostics"][0]["code"] == "plugin.config_invalid"
    assert "secret-value" not in output


def test_lint_plugins_resolves_relative_files_from_selected_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "config"
    work_dir = tmp_path / "elsewhere"
    config_dir.mkdir()
    work_dir.mkdir()
    config_file = config_dir / "stack.toml"
    config_file.touch()
    (config_dir / "catalogue.json").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(work_dir)
    monkeypatch.setattr("groundworkers.server.load_stack_config", lambda _path: object())
    monkeypatch.setattr(
        "groundworkers.plugins.discover_plugins", lambda: [RelativeCatalogueLinter()]
    )

    main(["lint-plugins", "--config-path", str(config_file)])

    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True


def test_serve_sets_explicit_config_before_building_app_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "stack.toml"
    config_file.touch()

    def check_selected_config(*, config_path: str | None) -> object:
        assert config_path == str(config_file)
        assert os.environ["OA_CONFIG_PATH"] == str(config_file.resolve())
        raise ConfigurationError("stop after checking config selection")

    monkeypatch.setattr("groundworkers.server.build_app_config", check_selected_config)

    with pytest.raises(SystemExit):
        main(["--config-path", str(config_file), "serve"])
