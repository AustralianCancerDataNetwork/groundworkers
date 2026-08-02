from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationSnapshot,
    ConnectionResult,
)


@dataclass
class SetupSession:
    """Mounted TUI state; all I/O remains in headless setup services."""

    config_path: str | Path | None = None
    profile: str | None = None
    ownership: ConfigurationOwnership = field(default_factory=ConfigurationOwnership)
    configuration: ConfigurationSnapshot = field(init=False)
    connection_results: tuple[ConnectionResult, ...] = ()

    def __post_init__(self) -> None:
        self.refresh_configuration()

    def refresh_configuration(self) -> None:
        self.configuration = load_configuration(
            config_path=self.config_path,
            profile=self.profile,
            ownership=self.ownership,
        )
        self.connection_results = ()

    @property
    def databases_connected(self) -> bool:
        return bool(self.connection_results) and all(
            result.connected for result in self.connection_results
        )


def load_tui_state(
    *, config_path: str | Path | None = None, profile: str | None = None
) -> SetupSession:
    return SetupSession(config_path=config_path, profile=profile)


TuiState = SetupSession

__all__ = ["SetupSession", "TuiState", "load_tui_state"]
