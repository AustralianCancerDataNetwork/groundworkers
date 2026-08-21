"""Secret-safe integration commands for a verified setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from groundworkers.application.setup.models import ConfigurationSnapshot
from groundworkers.config import GroundworkersConfig


@dataclass(frozen=True)
class IntegrationOutput:
    stdio_command: str
    http_command: str


def build_integration_output(snapshot: ConfigurationSnapshot) -> IntegrationOutput | None:
    """Return exact launch commands once the configuration resolves."""

    if not snapshot.usable or snapshot.stack is None:
        return None
    config_path = str(Path(snapshot.path).expanduser())
    try:
        config = GroundworkersConfig.validate_candidate(snapshot.stack)
    except (KeyError, TypeError, ValueError):
        return None
    prefix = f"groundworkers --config-path {quote(config_path)}"
    return IntegrationOutput(
        stdio_command=f"{prefix} --transport stdio",
        http_command=(
            f"{prefix} --transport streamable-http --host "
            f"{quote(config.mcp_host)} --port {config.mcp_port}"
        ),
    )


def quote(value: str) -> str:
    if value and all(character.isalnum() or character in "-_.:/" for character in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


__all__ = ["IntegrationOutput", "build_integration_output"]
