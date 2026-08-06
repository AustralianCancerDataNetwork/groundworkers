from __future__ import annotations

from groundskeeping.contracts import (
    DetailView,
    EmptyView,
    LoadingView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    TextView,
    ViewAction,
)

from groundworkers.application.setup.models import (
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
)
from groundworkers.tui.presenters.base import SetupPresenterBase


class EmbeddingsPresenter(SetupPresenterBase):
    def status(self, *, database_ready: bool, configured: bool) -> SemanticStatus:
        return (
            SemanticStatus.OK
            if database_ready and configured
            else SemanticStatus.WARNING
        )

    def landing(
        self,
        *,
        database_ready: bool,
        configuration: EmbeddingConfiguration | None,
        coverage: EmbeddingCoverageReport | None = None,
        selected_all: bool = True,
        selected_vocabularies: tuple[str, ...] = (),
    ) -> SurfaceView:
        if configuration is None:
            return EmptyView(
                title="Embedding setup not configured",
                message="Choose an embedding store, provider and model to continue.",
                status=SemanticStatus.WARNING,
            )
        if coverage is not None:
            return _setup_view_with_coverage(
                coverage,
                selected_all=selected_all,
                selected_vocabularies=selected_vocabularies,
            )
        return _configuration_view(configuration, database_ready=database_ready)

    def loading(self) -> LoadingView:
        return LoadingView(
            title="Embedding coverage",
            message="Counting CDM vocabularies and vector-store rows.",
            detail=(
                "This can take a moment for a full OMOP vocabulary. "
                "The TUI is waiting for the database queries to return."
            ),
        )

    def detail(
        self,
        configuration: EmbeddingConfiguration | None,
        coverage: EmbeddingCoverageReport | None,
        *,
        selected_all: bool = True,
        selected_vocabularies: tuple[str, ...] = (),
    ) -> DetailView:
        if configuration is None:
            return TextView(
                title="Vocabulary coverage",
                body="Configure an embedding store before checking vocabulary coverage.",
            )
        if coverage is None:
            return TextView(
                title="Vocabulary coverage",
                body="Refresh coverage to compare CDM vocabularies with the vector store.",
            )
        return _coverage_detail_view(coverage)


def _configuration_view(
    configuration: EmbeddingConfiguration,
    *,
    database_ready: bool,
) -> TableView:
    rows: list[TableRow] = [
        TableRow(
            key="embeddings.store",
            cells=_store_cells(configuration),
        ),
        TableRow(
            key="embeddings.provider",
            cells=(
                "Provider",
                f"{configuration.provider_kind} · {configuration.api_base}",
                "Not tested",
            ),
        ),
        TableRow(
            key="embeddings.model",
            cells=("Model", configuration.model_name, "Not tested"),
        ),
    ]
    if configuration.faiss_cache_dir is not None:
        rows.append(
            TableRow(
                key="embeddings.faiss_cache",
                cells=(
                    "FAISS cache",
                    configuration.faiss_cache_dir,
                    _path_status(configuration.faiss_cache_dir_exists),
                ),
            )
        )
    return TableView(
        title="Embedding setup",
        columns=("Component", "Configuration", "Status"),
        rows=tuple(rows),
        status=_embedding_status(configuration, database_ready=database_ready),
        message=_embedding_message(configuration, database_ready=database_ready),
        actions=(
            ViewAction("embeddings.refresh_coverage", "Refresh coverage"),
            ViewAction("embeddings.populate", "Populate", disabled=True),
        ),
    )


def _setup_view_with_coverage(
    report: EmbeddingCoverageReport,
    *,
    selected_all: bool,
    selected_vocabularies: tuple[str, ...],
) -> TableView:
    rows = _setup_rows_with_coverage(report)
    status = _coverage_status(report)
    coverage = report.coverage
    return TableView(
        title="Embedding setup",
        columns=("Component", "Configuration", "Status"),
        rows=tuple(rows),
        status=status,
        message=_coverage_message(report),
        actions=(
            ViewAction("embeddings.refresh_coverage", "Refresh coverage"),
            ViewAction(
                "embeddings.populate",
                "Populate",
                variant="primary",
                disabled=(
                    not coverage.available
                    or coverage.pending_total <= 0
                    or (not selected_all and not selected_vocabularies)
                ),
            ),
        ),
    )


def _setup_detail_view(report: EmbeddingCoverageReport) -> TableView:
    return TableView(
        title="Embedding setup",
        columns=("Component", "Configuration", "Status"),
        rows=tuple(_setup_rows_with_coverage(report)),
        status=_coverage_status(report),
        message=_coverage_message(report),
    )


def _setup_rows_with_coverage(
    report: EmbeddingCoverageReport,
) -> tuple[TableRow, ...]:
    coverage = report.coverage
    index = report.index
    rows: list[TableRow] = [
        TableRow(
            key="embeddings.store",
            cells=_store_cells(report.configuration),
        ),
        TableRow(
            key="embeddings.provider",
            cells=(
                "Provider",
                f"{report.configuration.provider_kind} · {report.configuration.api_base}",
                "Configured",
            ),
        ),
        TableRow(
            key="embeddings.model",
            cells=("Model", report.configuration.model_name, "Configured"),
        ),
        TableRow(
            key="embeddings.index",
            cells=("Index", index.display, _index_status(index)),
        ),
    ]
    if coverage.available:
        rows.append(
            TableRow(
                key="embeddings.coverage",
                cells=(
                    "Coverage",
                    (
                        f"{coverage.embedded_total:,} of {coverage.eligible_total:,} "
                        f"eligible concepts stored"
                    ),
                    f"{coverage.pending_total:,} missing",
                ),
            )
        )
        rows.append(
            TableRow(
                key="embeddings.scope",
                cells=(
                    "Scope",
                    "standard concepts"
                    if coverage.scope.standard_only
                    else "all concepts",
                    f"{len(coverage.rows):,} vocabularies",
                ),
            )
        )
    else:
        rows.append(
            TableRow(
                key="embeddings.coverage",
                cells=(
                    "Coverage",
                    coverage.blocker or "Coverage could not be loaded.",
                    "Unavailable",
                ),
            )
        )
    rows.extend(_index_warning_rows(index))
    return tuple(rows)


def _store_cells(configuration: EmbeddingConfiguration) -> tuple[str, str, str]:
    if configuration.backend == "sqlitevec":
        return (
            "Store",
            f"sqlitevec · {configuration.sqlite_path or '(path missing)'}",
            _path_status(configuration.sqlite_path_exists),
        )
    if configuration.backend == "pgvector":
        return ("Store", "pgvector database resource", "See Database")
    if configuration.backend == "faiss":
        return ("Store", "FAISS configured as backend", "Invalid")
    return ("Store", configuration.backend, "Unsupported")


def _embedding_status(
    configuration: EmbeddingConfiguration,
    *,
    database_ready: bool,
) -> SemanticStatus:
    if configuration.backend not in {"pgvector", "sqlitevec"}:
        return SemanticStatus.ERROR
    if configuration.backend == "pgvector" and not database_ready:
        return SemanticStatus.WARNING
    if configuration.sqlite_path_exists is False:
        return SemanticStatus.WARNING
    if configuration.faiss_cache_dir_exists is False:
        return SemanticStatus.WARNING
    return SemanticStatus.OK


def _embedding_message(
    configuration: EmbeddingConfiguration,
    *,
    database_ready: bool,
) -> str:
    if configuration.backend == "faiss":
        return "FAISS is a cache accelerator; set backend to sqlitevec or pgvector and configure faiss_cache_dir."
    if configuration.backend == "pgvector" and database_ready:
        return "pgvector store metadata is verified on the Database screen."
    if configuration.backend == "sqlitevec":
        return "File-backed embedding setup is checked here; provider encoding is verified separately."
    return "Configuration is visible, but database targets are not verified."


def _path_status(exists: bool | None) -> str:
    if exists is True:
        return "Found"
    if exists is False:
        return "Missing"
    return "Not configured"


def _coverage_detail_view(report: EmbeddingCoverageReport) -> TableView | TextView:
    coverage = report.coverage
    if not coverage.available:
        return TextView(
            title="Vocabulary coverage",
            body=coverage.blocker or "Coverage could not be loaded.",
        )
    coverage_percent = (
        (coverage.embedded_total / coverage.eligible_total) * 100
        if coverage.eligible_total
        else 0.0
    )
    return TableView(
        title="Vocabulary coverage",
        columns=("Vocabulary", "CDM", "Vector store", "Missing", "Coverage"),
        rows=(
            TableRow(
                key="embeddings.coverage.all",
                cells=(
                    "All vocabularies",
                    f"{coverage.eligible_total:,}",
                    f"{coverage.embedded_total:,}",
                    f"{coverage.pending_total:,}",
                    f"{coverage_percent:.1f}%",
                ),
            ),
            *(
                TableRow(
                    key=f"embeddings.coverage.{row.vocabulary}",
                    cells=(
                        row.vocabulary,
                        f"{row.eligible:,}",
                        f"{row.embedded:,}",
                        f"{row.pending:,}",
                        f"{row.coverage_percent:.1f}%",
                    ),
                )
                for row in coverage.rows
            ),
        ),
        status=SemanticStatus.WARNING if coverage.pending_total else SemanticStatus.OK,
        message=(
            f"{coverage.embedded_total:,} of {coverage.eligible_total:,} eligible "
            f"concepts stored for {coverage.scope.model_name}."
        ),
    )


def _index_warning_rows(index) -> tuple[TableRow, ...]:
    if index.insert_warning is None:
        return ()
    rows = [
        TableRow(
            key="embeddings.index_warning",
            cells=(
                "! Index warning",
                "Adding over an existing physical vector index will be slow.",
                "Drop before large runs",
            ),
        ),
        TableRow(
            key="embeddings.index_rebuild",
            cells=(
                "  After population",
                "Rebuild the vector index with omop-emb maintenance once inserts finish.",
                "Required",
            ),
        ),
    ]
    for offset, sql in enumerate(index.drop_sql, start=1):
        rows.append(
            TableRow(
                key=f"embeddings.index_drop.{offset}",
                cells=(f"  Drop SQL {offset}", sql, "Suggested"),
            )
        )
    return tuple(rows)


def _coverage_status(report: EmbeddingCoverageReport) -> SemanticStatus:
    if not report.coverage.available:
        return SemanticStatus.ERROR
    if report.coverage.pending_total or report.index.insert_warning is not None:
        return SemanticStatus.WARNING
    return SemanticStatus.OK


def _coverage_message(report: EmbeddingCoverageReport) -> str:
    if not report.coverage.available:
        return "Vocabulary coverage could not be loaded."
    return (
        f"{report.coverage.pending_total:,} missing concept embeddings across "
        f"{len(report.coverage.rows):,} vocabularies."
    )


def _index_status(index) -> str:
    if index.insert_warning is not None:
        return "Warning"
    if index.registered:
        return "Ready"
    return "Unregistered"
