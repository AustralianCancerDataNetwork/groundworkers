"""Getting OA_CONFIG_PATH into, and out of, the environment in time.

oa-configurator computes ``CONFIG_PATH`` from the environment once, while
:mod:`oa_configurator.loader` is being imported, and ``groundworkers.server``
imports it at module level. Everything here therefore has to happen before that
import, in ``groundworkers/__init__.py``, or it cannot happen at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def test_a_missing_config_path_does_not_kill_the_process_on_import() -> None:
    """oa-configurator raises FileNotFoundError for it from a module-level
    constant, so the server died before argparse ran and --config-path could not
    rescue it."""
    result = _run(
        """
import groundworkers.server
from groundworkers._env import rejected_config_path
print(rejected_config_path())
""",
        OA_CONFIG_PATH="/opt/.omop/config.toml",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/opt/.omop/config.toml"


def test_serving_refuses_a_silent_fallback_to_the_default() -> None:
    """Dropping the variable keeps the console reachable. A server that went on
    to answer from the default vocabulary instead would be worse than one that
    did not start."""
    result = _run(
        """
from groundworkers.server import main
main(["--describe"])
""",
        OA_CONFIG_PATH="/opt/.omop/config.toml",
    )

    assert result.returncode != 0
    assert "OA_CONFIG_PATH points at /opt/.omop/config.toml" in result.stderr
    assert "--config-path" in result.stderr


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
