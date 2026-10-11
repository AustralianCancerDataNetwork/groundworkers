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

_INDEX_DIAGNOSTICS = (
    (
        "performance.graph.fulltext",
        "Graph",
        "Full-text indexes",
        "database.graph",
        (
            "fulltext_sidecar_missing",
            "fulltext_indexes_missing",
            "fulltext_sidecar_unpopulated",
        ),
        "fulltext_indexes_present",
    ),
    (
        "performance.graph.functional",
        "Graph",
        "Functional text indexes",
        "database.graph",
        ("functional_indexes_missing",),
        "functional_indexes_present",
    ),
    (
        "performance.groundworkers.trigram",
        "Groundworkers",
        "Trigram indexes",
        "database.groundworkers",
        ("trigram_indexes_missing", "trigram_indexes_unchecked"),
        "trigram_indexes_present",
    ),
    (
        "performance.groundworkers.normalized",
        "Groundworkers",
        "Normalized-name indexes",
        "database.groundworkers",
        ("normalized_indexes_missing",),
        "normalized_indexes_present",
    ),
)


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
                "Graph indexes are prepared from Graph Setup. Groundworkers trigram, "
                "normalized-name and embedding indexes can be prepared here when "
                "their backends support it."
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
        index_rows = tuple(
            _status_row(
                key,
                area,
                index,
                _diagnostic_outcome(
                    by_key.get(target_key),
                    missing_codes,
                    present_code=present_code,
                ),
            )
            for key, area, index, target_key, missing_codes, present_code in _INDEX_DIAGNOSTICS
        )
        embedding = _embedding_index_outcome(
            embedding_configuration,
            embedding_coverage,
        )
        return (
            *index_rows,
            _status_row(
                "performance.embeddings.index", "Embeddings", "Vector index", embedding
            ),
        )


def _status_row(
    key: str,
    area: str,
    index: str,
    outcome: tuple[str, SemanticStatus],
) -> tuple[tuple[str, str, str, str], SemanticStatus]:
    return ((key, area, index, outcome[0]), outcome[1])


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
    if any(
        code in codes and codes[code].severity is DiagnosticSeverity.WARNING
        for code in missing_codes
    ):
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
