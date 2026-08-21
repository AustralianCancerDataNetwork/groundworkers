from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from groundworkers._env import record_config_path
from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationSnapshot,
    ConnectionResult,
    EmbeddingCoverageReport,
    LlmProviderCheckResult,
    ModelReconciliation,
)


@dataclass
class SetupSession:
    """Mounted TUI state; all I/O remains in headless setup services."""

    config_path: str | Path | None = None
    # Set only by the location wizard, which is the one place an operator says
    # "this is where my configuration lives" rather than merely "read this file".
    # A --config-path used to poke at a scratch config must not repoint the
    # machine, so this defaults off and nothing else turns it on.
    record_location: bool = False
    ownership: ConfigurationOwnership = field(default_factory=ConfigurationOwnership)
    configuration: ConfigurationSnapshot = field(init=False)
    connection_results: tuple[ConnectionResult, ...] = ()
    llm_provider_result: LlmProviderCheckResult | None = None
    embedding_coverage: EmbeddingCoverageReport | None = None
    # None means unchecked, not "nothing wrong": the Embeddings section reports
    # IDLE until this is filled in.
    embedding_reconciliation: ModelReconciliation | None = None
    embedding_standard_only: bool = True
    embedding_vocabulary_selection_all: bool = True
    embedding_selected_vocabularies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.refresh_configuration()

    def refresh_configuration(self) -> None:
        self.configuration = load_configuration(
            config_path=self.config_path,
            ownership=self.ownership,
        )
        if self.record_location and self.configuration.usable:
            # Deferred to here rather than done when the location was chosen:
            # OA_CONFIG_PATH naming a file that is not there stops the next
            # process inside an import, so the pointer is only written once the
            # configuration at the other end of it exists. Every save refreshes,
            # so a config created at a chosen location is recorded as it becomes
            # real.
            record_config_path(self.configuration.path)
        self.connection_results = ()
        self.llm_provider_result = None
        self.embedding_coverage = None
        self.embedding_reconciliation = None
        self.embedding_vocabulary_selection_all = True
        self.embedding_selected_vocabularies = ()

    @property
    def databases_connected(self) -> bool:
        return bool(self.connection_results) and all(
            result.connected for result in self.connection_results
        )


def load_tui_state(
    *,
    config_path: str | Path | None = None,
    ownership: ConfigurationOwnership | None = None,
) -> SetupSession:
    return SetupSession(config_path=config_path, ownership=ownership or ConfigurationOwnership())


TuiState = SetupSession

__all__ = ["SetupSession", "TuiState", "load_tui_state"]
