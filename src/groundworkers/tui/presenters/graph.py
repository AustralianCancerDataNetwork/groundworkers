from __future__ import annotations

from groundskeeping.contracts import (
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
)

from groundworkers.application.setup.models import GraphConfiguration


class GraphPresenter:
    def status(
        self, *, database_ready: bool, configuration: GraphConfiguration | None
    ) -> SemanticStatus:
        return SemanticStatus.WARNING

    def landing(
        self, *, database_ready: bool, configuration: GraphConfiguration | None
    ) -> SurfaceView:
        if configuration is None:
            return EmptyView(
                title="Graph not configured",
                message="Choose the shared CDM resource and graph traversal limits.",
                status=SemanticStatus.WARNING,
            )
        return TableView(
            title="Graph configuration",
            columns=("Setting", "Value", "Status"),
            rows=(
                TableRow(
                    key="graph.resource",
                    cells=("CDM resource", configuration.resource_name, "Configured"),
                ),
                TableRow(
                    key="graph.max_depth",
                    cells=("Maximum depth", str(configuration.max_depth), "Configured"),
                ),
                TableRow(
                    key="graph.max_paths",
                    cells=("Maximum paths", str(configuration.max_paths), "Configured"),
                ),
            ),
            status=SemanticStatus.WARNING,
            message=(
                "Graph traversal uses the verified CDM and vocabulary database."
                if database_ready
                else "Configuration is visible, but the CDM database is not verified."
            ),
        )
