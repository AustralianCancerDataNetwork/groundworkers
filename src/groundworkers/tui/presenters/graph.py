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
    ConnectionResult,
    DiagnosticSeverity,
    GraphConfiguration,
)
from groundworkers.tui.presenters.base import SetupPresenterBase


class GraphPresenter(SetupPresenterBase):
    def status(
        self,
        *,
        database_ready: bool,
        configuration: GraphConfiguration | None,
        readiness: ConnectionResult | None = None,
    ) -> SemanticStatus:
        """Report the graph's own readiness, not just that a config exists.

        The readiness target already checks everything the graph needs --
        vocabulary tables, relationship-classification tables, full-text and
        functional indexes -- so this reflects that result rather than assuming
        the worst. A clean run is OK; an unrun one is IDLE rather than a warning,
        because nothing is known to be wrong yet.
        """
        if configuration is None:
            return SemanticStatus.WARNING
        if readiness is None:
            return SemanticStatus.IDLE
        if not readiness.connected:
            return SemanticStatus.ERROR
        return _diagnostic_status(readiness)

    def landing(
        self,
        *,
        database_ready: bool,
        configuration: GraphConfiguration | None,
        readiness: ConnectionResult | None = None,
    ) -> SurfaceView:
        if configuration is None:
            return EmptyView(
                title="Graph not configured",
                message="Configure a CDM database to enable graph traversal.",
                status=SemanticStatus.WARNING,
            )
        return TableView(
            title="Graph configuration",
            columns=("Setting", "Value", "Status"),
            rows=(
                TableRow(
                    key="graph.cdm_database",
                    cells=(
                        "CDM database",
                        configuration.cdm_database_name,
                        "Configured",
                    ),
                ),
                TableRow(
                    key="graph.vocabulary_schema",
                    cells=(
                        "Vocabulary schema",
                        configuration.vocabulary_schema or "Same as CDM schema",
                        "Configured",
                    ),
                ),
                TableRow(
                    key="graph.grounding_max_depth",
                    cells=(
                        "Grounding depth",
                        str(configuration.grounding_max_depth),
                        "Configured",
                    ),
                ),
                TableRow(
                    key="graph.min_fulltext_overlap",
                    cells=(
                        "Minimum full-text overlap",
                        f"{configuration.min_fulltext_overlap:.2f}",
                        "Configured",
                    ),
                ),
            ),
            status=self.status(
                database_ready=database_ready,
                configuration=configuration,
                readiness=readiness,
            ),
            actions=(
                ViewAction("graph.prepare", "Prepare graph", variant="primary"),
            ),
            message=(
                "Graph traversal uses the verified CDM and vocabulary database."
                if database_ready
                else "Configuration is visible, but the CDM database is not verified."
            ),
        )


def _diagnostic_status(result: ConnectionResult) -> SemanticStatus:
    severities = {diagnostic.severity for diagnostic in result.diagnostics}
    if DiagnosticSeverity.ERROR in severities:
        return SemanticStatus.ERROR
    if DiagnosticSeverity.WARNING in severities:
        return SemanticStatus.WARNING
    return SemanticStatus.OK
