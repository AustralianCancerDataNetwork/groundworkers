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
    DiagnosticSeverity,
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
    ModelReconciliation,
)
from groundworkers.tui.presenters.base import SetupPresenterBase


class EmbeddingsPresenter(SetupPresenterBase):
    def status(
        self,
        *,
        database_ready: bool,
        configured: bool,
        reconciliation: ModelReconciliation | None = None,
    ) -> SemanticStatus:
        """Report what the tier can do, not that two names resolve.

        Two references resolving used to read as OK, which is how a store that
        was never reachable and a model that was never registered both showed
        green. Follows the Graph section instead: unchecked is IDLE, because
        nothing is known to be wrong yet, and a checked tier reports what the
        check found.
        """
        if not configured:
            return SemanticStatus.WARNING
        if reconciliation is None:
            return SemanticStatus.IDLE
        return _reconciliation_status(reconciliation)

    def landing(
        self,
        *,
        database_ready: bool,
        configuration: EmbeddingConfiguration | None,
        coverage: EmbeddingCoverageReport | None = None,
        reconciliation: ModelReconciliation | None = None,
        selected_all: bool = True,
        selected_vocabularies: tuple[str, ...] = (),
    ) -> SurfaceView:
        if configuration is None:
            return EmptyView(
                title="Embedding setup not configured",
                message=(
                    "Configure an embedding model, then add a vector store entry to "
                    "hold its vectors."
                ),
                status=SemanticStatus.WARNING,
                actions=(
                    ViewAction(
                        "embeddings.configure_model",
                        "Configure model",
                        variant="primary",
                    ),
                ),
            )
        if coverage is not None:
            return _setup_view_with_coverage(
                coverage,
                reconciliation=reconciliation,
                selected_all=selected_all,
                selected_vocabularies=selected_vocabularies,
            )
        return _configuration_view(
            configuration,
            database_ready=database_ready,
            reconciliation=reconciliation,
        )

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
    reconciliation: ModelReconciliation | None = None,
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
                _provider_label(configuration),
                _provider_status(reconciliation),
            ),
        ),
        TableRow(
            key="embeddings.model",
            cells=(
                "Model",
                _model_label(configuration),
                _model_status(configuration, reconciliation),
            ),
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
    rows.extend(_reconciliation_rows(reconciliation))
    return TableView(
        title="Embedding setup",
        columns=("Component", "Configuration", "Status"),
        rows=tuple(rows),
        status=_embedding_status(
            configuration,
            database_ready=database_ready,
            reconciliation=reconciliation,
        ),
        message=_embedding_message(
            configuration,
            database_ready=database_ready,
            reconciliation=reconciliation,
        ),
        actions=(
            ViewAction("embeddings.check_model", "Check model"),
            ViewAction("embeddings.refresh_coverage", "Refresh coverage"),
            ViewAction("embeddings.populate", "Populate", disabled=True),
            ViewAction("embeddings.configure_model", "Configure model"),
            ViewAction("embeddings.initialize_store", "Initialize embedding store"),
        ),
    )


def _setup_view_with_coverage(
    report: EmbeddingCoverageReport,
    *,
    reconciliation: ModelReconciliation | None = None,
    selected_all: bool,
    selected_vocabularies: tuple[str, ...],
) -> TableView:
    rows = _setup_rows_with_coverage(report, reconciliation=reconciliation)
    coverage = report.coverage
    return TableView(
        title="Embedding setup",
        columns=("Component", "Configuration", "Status"),
        rows=tuple(rows),
        status=_coverage_status(report, reconciliation=reconciliation),
        message=_coverage_message(report, reconciliation=reconciliation),
        actions=(
            ViewAction("embeddings.check_model", "Check model"),
            ViewAction("embeddings.refresh_coverage", "Refresh coverage"),
            ViewAction(
                "embeddings.populate",
                "Populate",
                variant="primary",
                disabled=(
                    not coverage.available
                    or coverage.pending_total <= 0
                    or (not selected_all and not selected_vocabularies)
                    # A run that cannot encode, or cannot reach the store, fails
                    # after the operator has committed to it.
                    or (
                        reconciliation is not None
                        and not reconciliation.ready_for_population
                    )
                ),
            ),
            ViewAction("embeddings.configure_model", "Configure model"),
            ViewAction("embeddings.initialize_store", "Initialize embedding store"),
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
    *,
    reconciliation: ModelReconciliation | None = None,
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
                _provider_label(report.configuration),
                _provider_status(reconciliation),
            ),
        ),
        TableRow(
            key="embeddings.model",
            cells=(
                "Model",
                _model_label(report.configuration),
                _model_status(report.configuration, reconciliation),
            ),
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
    rows.extend(_reconciliation_rows(reconciliation))
    return tuple(rows)


def _store_cells(configuration: EmbeddingConfiguration) -> tuple[str, str, str]:
    if configuration.backend == "sqlitevec":
        return (
            "Store",
            (
                f"{configuration.vector_store_name} · sqlitevec · "
                f"{configuration.database_path or configuration.database_name}"
            ),
            _path_status(configuration.database_path_exists),
        )
    if configuration.backend == "pgvector":
        return (
            "Store",
            (
                f"{configuration.vector_store_name} · pgvector · "
                f"{configuration.database_name}"
            ),
            "See Database",
        )
    return (
        "Store",
        f"{configuration.vector_store_name} · {configuration.backend}",
        "Unsupported",
    )


def _embedding_status(
    configuration: EmbeddingConfiguration,
    *,
    database_ready: bool,
    reconciliation: ModelReconciliation | None = None,
) -> SemanticStatus:
    if configuration.backend not in {"pgvector", "sqlitevec"}:
        return SemanticStatus.ERROR
    if not configuration.embeddings_supported:
        return SemanticStatus.ERROR
    if reconciliation is not None:
        reconciled = _reconciliation_status(reconciliation)
        if reconciled is not SemanticStatus.OK:
            return reconciled
    if configuration.backend == "pgvector" and not database_ready:
        return SemanticStatus.WARNING
    if configuration.database_path_exists is False:
        return SemanticStatus.WARNING
    if configuration.faiss_cache_dir_exists is False:
        return SemanticStatus.WARNING
    return SemanticStatus.OK if reconciliation is not None else SemanticStatus.IDLE


def _embedding_message(
    configuration: EmbeddingConfiguration,
    *,
    database_ready: bool,
    reconciliation: ModelReconciliation | None = None,
) -> str:
    if configuration.backend not in {"sqlitevec", "pgvector"}:
        return "Choose a supported vector store backend: sqlitevec or pgvector. FAISS can be configured separately as a query cache."
    if not configuration.embeddings_supported:
        return "The selected model is not declared as embedding-capable. Choose an embedding model before population."
    if reconciliation is None:
        return (
            "Two references resolve. Run Check model to find out whether the store "
            "holds this model and the provider can encode with it."
        )
    return _reconciliation_message(reconciliation)


def _reconciliation_status(reconciliation: ModelReconciliation) -> SemanticStatus:
    severity = reconciliation.worst_severity
    if severity is DiagnosticSeverity.ERROR:
        return SemanticStatus.ERROR
    if severity is DiagnosticSeverity.WARNING:
        return SemanticStatus.WARNING
    return SemanticStatus.OK


def _reconciliation_message(reconciliation: ModelReconciliation) -> str:
    """Lead with the first thing standing in the way, if anything is."""

    for severity in (DiagnosticSeverity.ERROR, DiagnosticSeverity.WARNING):
        blocking = next(
            (
                item
                for item in reconciliation.diagnostics
                if item.severity is severity
            ),
            None,
        )
        if blocking is not None:
            return blocking.message
    if not reconciliation.model_is_registered:
        return (
            "The provider can encode with this model. It is not in the store's "
            "registry yet; population will register it."
        )
    return "The store holds this model and the provider can encode with it."


def _provider_status(reconciliation: ModelReconciliation | None) -> str:
    if reconciliation is None:
        return "Not checked"
    provider = reconciliation.provider
    if provider is None:
        return "Not configured"
    if not provider.reachable:
        return "Unreachable"
    if not provider.encoding_succeeded:
        return "Cannot encode"
    return "Encoding"


def _model_status(
    configuration: EmbeddingConfiguration,
    reconciliation: ModelReconciliation | None,
) -> str:
    if not configuration.embeddings_supported:
        return "Not embedding-capable"
    if reconciliation is None:
        return "Not checked"
    if reconciliation.model_is_registered:
        return "Registered"
    return "Not registered"


def _reconciliation_rows(
    reconciliation: ModelReconciliation | None,
) -> tuple[TableRow, ...]:
    """One row per finding, so the verdict says why and not only how bad.

    Nothing is emitted before the check runs: an empty section reads as "no
    problems", which is exactly the claim an unrun check cannot make.
    """
    if reconciliation is None:
        return ()
    return tuple(
        TableRow(
            key=f"embeddings.diagnostic.{item.code}.{offset}",
            cells=(
                _SEVERITY_LABELS[item.severity],
                item.message,
                item.code,
            ),
        )
        for offset, item in enumerate(reconciliation.diagnostics, start=1)
    )


_SEVERITY_LABELS = {
    DiagnosticSeverity.ERROR: "! Blocker",
    DiagnosticSeverity.WARNING: "! Warning",
    DiagnosticSeverity.INFO: "  Note",
}


def _path_status(exists: bool | None) -> str:
    if exists is True:
        return "Found"
    if exists is False:
        return "Missing"
    return "Not configured"


def _provider_label(configuration: EmbeddingConfiguration) -> str:
    endpoint = configuration.api_base or "provider default"
    return (
        f"{configuration.provider_name} ({configuration.provider_kind}) · {endpoint}"
    )


def _model_label(configuration: EmbeddingConfiguration) -> str:
    return f"{configuration.model_entry_name} · {configuration.model_name}"


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


def _coverage_status(
    report: EmbeddingCoverageReport,
    *,
    reconciliation: ModelReconciliation | None = None,
) -> SemanticStatus:
    if reconciliation is not None:
        reconciled = _reconciliation_status(reconciliation)
        if reconciled is SemanticStatus.ERROR:
            return reconciled
    if not report.coverage.available:
        return SemanticStatus.ERROR
    if (
        report.coverage.pending_total
        or report.index.insert_warning is not None
        or (
            reconciliation is not None
            and _reconciliation_status(reconciliation) is SemanticStatus.WARNING
        )
    ):
        return SemanticStatus.WARNING
    return SemanticStatus.OK


def _coverage_message(
    report: EmbeddingCoverageReport,
    *,
    reconciliation: ModelReconciliation | None = None,
) -> str:
    # A blocker outranks the count: population cannot close the gap while it
    # stands, so reporting only the gap would send the operator at the button.
    if reconciliation is not None and not reconciliation.ready_for_population:
        return _reconciliation_message(reconciliation)
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
