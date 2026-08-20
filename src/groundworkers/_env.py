"""Pre-seed ``OA_CONFIG_PATH`` from a fixed, user-scoped env file.

Precedence for the config path is, in order of decreasing priority:

1. ``--config-path``
2. ``OA_CONFIG_PATH`` in the real environment (a shell export, an MCP client's
   ``env`` block)
3. ``OA_CONFIG_PATH`` in :data:`ENV_FILE`
4. ``~/.config/omop/config.toml``
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv, set_key, unset_key

__all__ = [
    "ENV_CONFIG_PATH",
    "env_file_path",
    "load_environment",
    "record_config_path",
    "rejected_config_path",
]

ENV_CONFIG_PATH = "OA_CONFIG_PATH"
ENV_FILE = Path("~/.config/omop/groundworkers.env")
_rejected: str | None = None

def env_file_path() -> Path:
    """The env file this module reads and writes."""
    return ENV_FILE.expanduser()

def rejected_config_path() -> str | None:
    """
    The ``OA_CONFIG_PATH`` that was dropped for naming an unusable file.
    ``None`` when the variable was unset or pointed at a real ``.toml``.
    """
    return _rejected

def load_environment() -> None:
    """Load :data:`ENV_FILE`, then disarm an unusable ``OA_CONFIG_PATH``.

    Runs before any import of ``oa_configurator``. Never raises: neither 
    an unreadable env file nor a misdirected variable should stop the 
    console from starting and offering to fix it.
    """

    global _rejected
    _rejected = None
    try:
        load_dotenv(env_file_path(), override=False)
    except OSError:
        pass
    _rejected = _disarm_unusable_config_path()


def _disarm_unusable_config_path() -> str | None:
    """
    Remove an ``OA_CONFIG_PATH`` oa-configurator would refuse at import.

    It rejects a path that does not exist and one whose suffix is not ``.toml``
    """

    raw = os.environ.get(ENV_CONFIG_PATH)
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.suffix == ".toml" and candidate.is_file():
        return None
    del os.environ[ENV_CONFIG_PATH]
    return raw


def record_config_path(path: str | Path) -> Path | None:
    """
    Record *path* as the config every later run should resolve.

    Writes ``OA_CONFIG_PATH`` into :data:`ENV_FILE`, or removes it when *path*
    is the location oa-configurator would resolve anyway.
    """

    from oa_configurator import DEFAULT_CONFIG_PATH  # local: import order matters

    resolved = Path(path).expanduser().resolve()
    destination = env_file_path()
    if resolved == Path(DEFAULT_CONFIG_PATH):
        return _clear(destination)
    if resolved.suffix != ".toml" or not resolved.is_file():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.touch(exist_ok=True)
    set_key(destination, ENV_CONFIG_PATH, str(resolved))
    os.environ[ENV_CONFIG_PATH] = str(resolved)
    return destination


def _clear(destination: Path) -> Path | None:
    if not destination.is_file():
        return None
    from dotenv import get_key

    if get_key(destination, ENV_CONFIG_PATH) is None:
        return None
    unset_key(destination, ENV_CONFIG_PATH)
    os.environ.pop(ENV_CONFIG_PATH, None)
    return destination
