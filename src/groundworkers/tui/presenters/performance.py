from __future__ import annotations

from collections.abc import Sequence

from groundskeeping.contracts import (
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    ViewAction,
)

from groundworkers.application.setup.models import (
    ConnectionResult,
    DiagnosticSeverity,
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
)
from groundworkers.tui.presenters.base import SetupPresenterBase


class PerformancePresenter(SetupPresenterBase):
    """Present index readiness without mixing it into database setup."""

    def status(
        self,
        *,
        connections: Sequence[ConnectionResult],
        embedding_configuration: EmbeddingConfiguration | None,
        embedding_coverage: EmbeddingCoverageReport | None,
    ) -> SemanticStatus:
        statuses = self._statuses(
            connections,
            embedding_configuration=embedding_configuration,
            embedding_coverage=embedding_coverage,
        )
        if all(status is SemanticStatus.IDLE for _, status in statuses):
            return SemanticStatus.IDLE
        if any(status is SemanticStatus.ERROR for _, status in statuses):
            return SemanticStatus.ERROR
        if any(status is SemanticStatus.WARNING for _, status in statuses):
            return SemanticStatus.WARNING
        return SemanticStatus.OK

    def landing(
        self,
        *,
        connections: Sequence[ConnectionResult],
        embedding_configuration: EmbeddingConfiguration | None,
        embedding_coverage: EmbeddingCoverageReport | None,
        can_prepare: bool,
    ) -> SurfaceView:
        statuses = self._statuses(
            connections,
            embedding_configuration=embedding_configuration,
            embedding_coverage=embedding_coverage,
        )
        rows = tuple(
            TableRow(
                key=key,
                cells=(area, index, outcome),
                detail=(
                    ("area", area),
                    ("index", index),
                    ("status", outcome),
                ),
            )
            for (key, area, index, outcome), _status in statuses
        )
        return TableView(
            title="Performance",
            columns=("Area", "Index", "Status"),
            rows=rows,
            status=self.status(
                connections=connections,
                embedding_configuration=embedding_configuration,
                embedding_coverage=embedding_coverage,
            ),
            actions=(
                ViewAction("performance.refresh", "Refresh"),
                ViewAction(
                    "performance.prepare",
                    "Prepare indexes",
                    variant="primary",
                    disabled=not can_prepare,
                ),
            ),
            message=(
                "Graph indexes are prepared from Graph Setup. Groundworkers trigram "
                "and embedding indexes can be prepared here when their backends support it."
            ),
        )

    def _statuses(
        self,
        connections: Sequence[ConnectionResult],
        *,
        embedding_configuration: EmbeddingConfiguration | None,
        embedding_coverage: EmbeddingCoverageReport | None,
    ) -> tuple[tuple[tuple[str, str, str, str], SemanticStatus], ...]:
        by_key = {result.target_key: result for result in connections}
        graph = by_key.get("database.graph")
        groundworkers = by_key.get("database.groundworkers")
        graph_fulltext = _diagnostic_outcome(
            graph,
            (
                "fulltext_sidecar_missing",
                "fulltext_indexes_missing",
                "fulltext_sidecar_unpopulated",
            ),
            present_code="fulltext_indexes_present",
        )
        graph_functional = _diagnostic_outcome(
            graph,
            ("functional_indexes_missing",),
            present_code="functional_indexes_present",
        )
        trigram = _diagnostic_outcome(
            groundworkers,
            ("trigram_indexes_missing", "trigram_indexes_unchecked"),
            present_code="trigram_indexes_present",
        )
        embedding = _embedding_index_outcome(
            embedding_configuration,
            embedding_coverage,
        )
        return (
            (("performance.graph.fulltext", "Graph", "Full-text indexes", graph_fulltext[0]), graph_fulltext[1]),
            (("performance.graph.functional", "Graph", "Functional text indexes", graph_functional[0]), graph_functional[1]),
            (("performance.groundworkers.trigram", "Groundworkers", "Trigram indexes", trigram[0]), trigram[1]),
            (("performance.embeddings.index", "Embeddings", "Vector index", embedding[0]), embedding[1]),
        )


def _diagnostic_outcome(
    result: ConnectionResult | None,
    missing_codes: tuple[str, ...],
    *,
    present_code: str,
) -> tuple[str, SemanticStatus]:
    if result is None:
        return "Not checked", SemanticStatus.IDLE
    if not result.connected:
        return "Connection failed", SemanticStatus.ERROR
    codes = {diagnostic.code: diagnostic for diagnostic in result.diagnostics}
    if any(code in codes and codes[code].severity is DiagnosticSeverity.WARNING for code in missing_codes):
        return "Missing", SemanticStatus.WARNING
    if present_code in codes:
        return "Ready", SemanticStatus.OK
    return "Not checked", SemanticStatus.IDLE


def _embedding_index_outcome(
    configuration: EmbeddingConfiguration | None,
    report: EmbeddingCoverageReport | None,
) -> tuple[str, SemanticStatus]:
    if configuration is None:
        return "Not configured", SemanticStatus.IDLE
    if report is None:
        return "Not checked", SemanticStatus.IDLE
    if not report.coverage.available:
        return "Unavailable", SemanticStatus.ERROR
    index = report.index
    if not index.registered:
        return "Not registered", SemanticStatus.WARNING
    if index.registry_index_type == "flat":
        return "Exact scan (no physical index)", SemanticStatus.OK
    if index.has_physical_index:
        return f"Ready ({index.registry_index_type or 'physical'})", SemanticStatus.OK
    return "Missing", SemanticStatus.WARNING


__all__ = ["PerformancePresenter"]
