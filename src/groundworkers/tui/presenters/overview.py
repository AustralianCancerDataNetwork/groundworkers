from __future__ import annotations

from groundskeeping.contracts import (
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    ViewAction,
)

from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    ConnectionResult,
    EmbeddingCoverageReport,
    LlmProviderCheckResult,
)
from groundworkers.tui.presenters.base import SetupPresenterBase


class OverviewPresenter(SetupPresenterBase):
    """Outcome checklist for required and optional setup capabilities."""

    def status(
        self,
        snapshot: ConfigurationSnapshot,
        *,
        connections: tuple[ConnectionResult, ...],
        embedding_coverage: EmbeddingCoverageReport | None,
        llm_result: LlmProviderCheckResult | None,
        graph_ready: bool,
        integration_ready: bool,
    ) -> SemanticStatus:
        if not snapshot.usable:
            return SemanticStatus.WARNING
        return SemanticStatus.OK if connections and all(item.connected for item in connections) else SemanticStatus.WARNING

    def landing(
        self,
        snapshot: ConfigurationSnapshot,
        *,
        connections: tuple[ConnectionResult, ...],
        embedding_coverage: EmbeddingCoverageReport | None,
        llm_result: LlmProviderCheckResult | None,
        graph_ready: bool,
        integration_ready: bool,
    ) -> SurfaceView:
        if not snapshot.usable:
            return EmptyView(
                title="Start Groundworkers",
                message=(
                    f"Configuration destination: {snapshot.path}. "
                    "Choose a location, then configure the required CDM connection."
                ),
                status=SemanticStatus.WARNING,
                actions=(ViewAction("database.configure", "Configure CDM", variant="primary"),),
            )
        cdm_ready = bool(connections) and all(result.connected for result in connections)
        embedding_ready = embedding_coverage is not None and embedding_coverage.coverage.available
        chat_ready = llm_result is not None and llm_result.ready
        rows = (
            _row("required.cdm", "CDM vocabulary", "Ready" if cdm_ready else "Needs verification", cdm_ready),
            _row("recommended.graph", "Search and graph", "Ready" if graph_ready else "Needs preparation", graph_ready, optional=True),
            _row("optional.embeddings", "Embeddings", "Ready" if embedding_ready else "Not configured or unchecked", embedding_ready, optional=True),
            _row("optional.chat", "Chat model", "Ready" if chat_ready else "Not configured or unchecked", chat_ready, optional=True),
            _row("integration", "Integration output", "Ready" if integration_ready else "Verify required capabilities first", integration_ready, optional=True),
        )
        overall = SemanticStatus.OK if cdm_ready else SemanticStatus.WARNING
        return TableView(
            title="Overview",
            columns=("Capability", "Outcome", "Class"),
            rows=rows,
            status=overall,
            actions=(
                ViewAction("overview.verify_all", "Verify all", variant="primary"),
                ViewAction("database.configure", "Configure CDM"),
                ViewAction("graph.prepare", "Prepare graph"),
                ViewAction("embeddings.configure_model", "Set up embeddings"),
                ViewAction("llm_provider.configure", "Set up chat model"),
                ViewAction("overview.integration", "Show integration output"),
            ),
            message="Optional capabilities remain neutral until configured; they do not make the core CDM service fail.",
        )


def _row(key: str, label: str, outcome: str, ready: bool, *, optional: bool = False) -> TableRow:
    return TableRow(
        key=key,
        cells=(label, outcome, "Optional" if optional else "Required"),
        detail=(("ready", str(ready)), ("outcome", outcome)),
    )


__all__ = ["OverviewPresenter"]
