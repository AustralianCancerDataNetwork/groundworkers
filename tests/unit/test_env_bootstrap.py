"""Getting OA_CONFIG_PATH into, and out of, the environment in time.

oa-configurator computes ``CONFIG_PATH`` from the environment once, while
:mod:`oa_configurator.loader` is being imported. The CLI resolves the locator
before importing the application modules; importing ``groundworkers`` itself
must remain side-effect free.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from oa_configurator import save_stack_config

from groundworkers import _env
from tests.support.stack_config import build_cdm_stack

REPO_SRC = str(Path(__file__).resolve().parents[2] / "src")


def _run(code: str, **environment: str) -> subprocess.CompletedProcess[str]:
    """Run *code* in a fresh interpreter, so import-time behaviour is real.

    An in-process test cannot exercise this: `oa_configurator.loader` is already
    imported by the time any test runs, and its CONFIG_PATH is already fixed.
    """
    env = {**os.environ, "PYTHONPATH": REPO_SRC, **environment}
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )


def test_a_recorded_path_is_in_the_environment_before_the_loader_reads_it(
    tmp_path: Path,
) -> None:
    """The whole point: a location chosen once is resolved by every later run,
    including a server an MCP client launches with no arguments and no shell."""
    config = tmp_path / "recorded.toml"
    save_stack_config(build_cdm_stack(), config)
    env_file = tmp_path / "groundworkers.env"
    env_file.write_text(f"OA_CONFIG_PATH={config}\n", encoding="utf-8")

    result = _run(
        f"""
import pathlib
from groundworkers import _env
_env.ENV_FILE = pathlib.Path({str(env_file)!r})
_env.load_environment()
from oa_configurator.loader import CONFIG_PATH
print(CONFIG_PATH)
"""
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(config.resolve())


def test_a_real_environment_variable_beats_the_recorded_one(tmp_path: Path) -> None:
    """A shell export or an MCP client's own env block is the more specific
    instruction, and load_dotenv does not override what is already set."""
    recorded = tmp_path / "recorded.toml"
    explicit = tmp_path / "explicit.toml"
    save_stack_config(build_cdm_stack(), recorded)
    save_stack_config(build_cdm_stack(), explicit)
    env_file = tmp_path / "groundworkers.env"
    env_file.write_text(f"OA_CONFIG_PATH={recorded}\n", encoding="utf-8")

    result = _run(
        f"""
import pathlib
from groundworkers import _env
_env.ENV_FILE = pathlib.Path({str(env_file)!r})
_env.load_environment()
from oa_configurator.loader import CONFIG_PATH
print(CONFIG_PATH)
""",
        OA_CONFIG_PATH=str(explicit),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(explicit.resolve())


def test_library_import_does_not_mutate_the_config_environment(tmp_path: Path) -> None:
    """Locator resolution belongs to the CLI boundary, not package import."""
    missing = tmp_path / "missing.toml"
    result = _run(
        """
import os
import groundworkers
print(os.environ.get("OA_CONFIG_PATH"))
""",
        OA_CONFIG_PATH=str(missing),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(missing)


def test_serving_refuses_a_silent_fallback_to_the_default(tmp_path: Path) -> None:
    """The server reports an unusable locator instead of reading another config."""
    missing = tmp_path / "missing.toml"
    result = _run(
        """
from groundworkers.server import main
main(["--describe"])
""",
        OA_CONFIG_PATH=str(missing),
    )

    assert result.returncode != 0
    assert f"OA_CONFIG_PATH points at {missing}" in result.stderr
    assert "--config-path" in result.stderr


@pytest.mark.parametrize(
    ("contents", "message"),
    (("", "incomplete or invalid"), ("[", "not valid TOML")),
)
def test_cli_config_failures_are_actionable_without_traceback(
    tmp_path: Path, contents: str, message: str
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(contents, encoding="utf-8")
    result = _run(
        f"""
from groundworkers.server import main
main(["--config-path", {str(config)!r}, "--describe"])
"""
    )

    assert result.returncode != 0
    assert message in result.stderr
    assert f"groundworkers tui --config-path {config}" in result.stderr
    assert "Traceback" not in result.stderr


def test_an_unusable_path_is_dropped_rather_than_left_to_raise(monkeypatch) -> None:
    monkeypatch.setenv(_env.ENV_CONFIG_PATH, "/nowhere/config.toml")

    _env.load_environment()

    assert _env.rejected_config_path() == "/nowhere/config.toml"
    assert _env.ENV_CONFIG_PATH not in os.environ


def test_a_path_that_is_not_toml_is_also_dropped(tmp_path: Path, monkeypatch) -> None:
    """oa-configurator refuses the suffix as hard as it refuses the absence."""
    other = tmp_path / "config.yaml"
    other.write_text("", encoding="utf-8")
    monkeypatch.setenv(_env.ENV_CONFIG_PATH, str(other))

    _env.load_environment()

    assert _env.rejected_config_path() == str(other)


def test_choosing_the_default_again_clears_a_recorded_path(tmp_path: Path) -> None:
    """Otherwise moving back is the one direction that does not stick."""
    from oa_configurator import DEFAULT_CONFIG_PATH

    recorded = tmp_path / "recorded.toml"
    save_stack_config(build_cdm_stack(), recorded)
    _env.record_config_path(recorded)
    assert str(recorded) in _env.env_file_path().read_text()

    _env.record_config_path(DEFAULT_CONFIG_PATH)

    assert "OA_CONFIG_PATH" not in _env.env_file_path().read_text()


def test_a_path_with_nothing_at_it_is_never_recorded(tmp_path: Path) -> None:
    """Recording one would make the next process fail to start at all, which is
    strictly worse than reading the default."""
    assert _env.record_config_path(tmp_path / "absent.toml") is None
    assert not _env.env_file_path().is_file()
