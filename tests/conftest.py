from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolated_env_file(tmp_path_factory, monkeypatch):
    """Keep the recorded config location out of the developer's home directory.

    `groundworkers._env.ENV_FILE` is a real path under ~/.config/omop, and the
    location wizard writes it. Autouse rather than opt-in: a test that records a
    location and forgets to redirect it repoints the machine Groundworkers reads
    from, which is not a failure any assertion would catch.
    """
    from groundworkers import _env

    monkeypatch.setattr(
        _env, "ENV_FILE", tmp_path_factory.mktemp("env") / "groundworkers.env"
    )
    monkeypatch.delenv(_env.ENV_CONFIG_PATH, raising=False)
    # `load_environment` writes this module global directly, so a test that calls
    # it leaves every later test looking like it was launched with a broken
    # OA_CONFIG_PATH -- which makes the setup page open the location wizard.
    monkeypatch.setattr(_env, "_rejected", None)
