from __future__ import annotations

from groundskeeping.contracts.views import SemanticStatus

from groundworkers.application.setup.models import (
    ConnectionResult,
    DiagnosticSeverity,
    GraphConfiguration,
    ResourceDiagnostic,
)
from groundworkers.tui.presenters.graph import GraphPresenter

CONFIG = GraphConfiguration(
    cdm_database_name="cdm_db",
    vocabulary_schema="public",
    grounding_max_depth=5,
    min_fulltext_overlap=0.0,
)


def _result(*diagnostics: ResourceDiagnostic, connected: bool = True) -> ConnectionResult:
    return ConnectionResult(
        target_key="database.graph",
        connected=connected,
        latency_ms=34.2,
        safe_url="postgresql://host/db",
        diagnostics=diagnostics,
    )


def _info(code: str) -> ResourceDiagnostic:
    return ResourceDiagnostic(code, "fine", DiagnosticSeverity.INFO)


def test_every_readiness_check_passing_reports_ok() -> None:
    """The whole point: a clean readiness run is green, not a standing warning."""
    readiness = _result(
        _info("vocabulary_tables_present"),
        _info("graph_classification_present"),
        _info("fulltext_indexes_present"),
        _info("functional_indexes_present"),
    )

    status = GraphPresenter().status(
        database_ready=True, configuration=CONFIG, readiness=readiness
    )

    assert status is SemanticStatus.OK


def test_a_missing_check_reports_warning() -> None:
    readiness = _result(
        _info("vocabulary_tables_present"),
        ResourceDiagnostic(
            "fulltext_indexes_missing", "missing", DiagnosticSeverity.WARNING
        ),
    )

    assert (
        GraphPresenter().status(
            database_ready=True, configuration=CONFIG, readiness=readiness
        )
        is SemanticStatus.WARNING
    )


def test_an_error_diagnostic_outranks_a_warning() -> None:
    readiness = _result(
        ResourceDiagnostic("a", "warn", DiagnosticSeverity.WARNING),
        ResourceDiagnostic("b", "bad", DiagnosticSeverity.ERROR),
    )

    assert (
        GraphPresenter().status(
            database_ready=True, configuration=CONFIG, readiness=readiness
        )
        is SemanticStatus.ERROR
    )


def test_an_unreachable_database_reports_error() -> None:
    assert (
        GraphPresenter().status(
            database_ready=False,
            configuration=CONFIG,
            readiness=_result(connected=False),
        )
        is SemanticStatus.ERROR
    )


def test_an_unrun_check_is_idle_not_a_warning() -> None:
    """Nothing is known to be wrong yet, so do not claim there is."""
    assert (
        GraphPresenter().status(
            database_ready=False, configuration=CONFIG, readiness=None
        )
        is SemanticStatus.IDLE
    )


def test_no_graph_configuration_reports_warning() -> None:
    assert (
        GraphPresenter().status(
            database_ready=True, configuration=None, readiness=None
        )
        is SemanticStatus.WARNING
    )


def test_the_landing_view_carries_the_same_status() -> None:
    readiness = _result(_info("fulltext_indexes_present"))

    view = GraphPresenter().landing(
        database_ready=True, configuration=CONFIG, readiness=readiness
    )

    assert view.status is SemanticStatus.OK
