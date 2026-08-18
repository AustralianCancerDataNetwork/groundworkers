from __future__ import annotations

from groundskeeping.contracts import (
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
)

from groundworkers.application.setup.models import GraphConfiguration
from groundworkers.tui.presenters.base import SetupPresenterBase


class GraphPresenter(SetupPresenterBase):
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
            status=SemanticStatus.WARNING,
            message=(
                "Graph traversal uses the verified CDM and vocabulary database."
                if database_ready
                else "Configuration is visible, but the CDM database is not verified."
            ),
        )
