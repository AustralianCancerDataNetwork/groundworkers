from __future__ import annotations

from collections.abc import Sequence

from groundskeeping.contracts import (
    EmptyView,
    KeyValueView,
    LoadingView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    ViewAction,
)

from groundworkers.application.setup.models import (
    ClassifiedFailure,
    ConfigurationSnapshot,
    ConfigurationState,
    ConnectionResult,
    DatabaseTarget,
    DiagnosticSeverity,
)
from groundworkers.tui.presenters.base import (
    DetailRows,
    SetupPresenterBase,
    detail_row,
)


class DatabasePresenter(SetupPresenterBase):
    def status(
        self,
        snapshot: ConfigurationSnapshot,
        results: Sequence[ConnectionResult],
    ) -> SemanticStatus:
        config_status = _configuration_status(snapshot)
        return (
            config_status
            if config_status is not SemanticStatus.OK
            else _connection_status(results)
        )

    def landing(
        self,
        snapshot: ConfigurationSnapshot,
        targets: Sequence[DatabaseTarget],
        results: Sequence[ConnectionResult],
        *,
        selected_target_key: str | None = None,
    ) -> SurfaceView:
        if snapshot.state is ConfigurationState.MISSING:
            return EmptyView(
                title="No stack configuration found",
                message=(
                    "Create a database connection and map the CDM and vocabulary "
                    "schemas before continuing."
                ),
                status=SemanticStatus.WARNING,
                actions=(
                    ViewAction("database.configure", "Configure", variant="primary"),
                    ViewAction("database.refresh", "Refresh"),
                ),
            )
        if snapshot.state in {
            ConfigurationState.MALFORMED,
            ConfigurationState.INCOMPLETE,
        }:
            return TableView(
                title=(
                    "Configuration needs repair"
                    if snapshot.state is ConfigurationState.MALFORMED
                    else "Configuration is incomplete"
                ),
                columns=("Area", "Issue"),
                rows=tuple(
                    TableRow(
                        key=f"config.issue.{index}",
                        cells=(issue.field or "configuration", issue.message),
                    )
                    for index, issue in enumerate(snapshot.issues)
                ),
                status=SemanticStatus.ERROR,
                message=str(snapshot.path),
                actions=(
                    ViewAction("database.configure", "Configure", variant="primary"),
                    ViewAction("database.refresh", "Refresh"),
                ),
            )

        result_by_key = {item.target_key: item for item in results}
        return TableView(
            title="Database resources",
            columns=("Resource", "Database", "Schemas", "Status", "Latency"),
            rows=tuple(
                _target_row(target, result_by_key.get(target.key)) for target in targets
            ),
            status=_connection_status(results),
            message=f"{snapshot.path}  |  {snapshot.ownership.mode.value}",
            actions=_database_actions(selected_target_key),
        )

    def loading(self) -> LoadingView:
        return LoadingView(
            title="Testing connections",
            message="Checking the configured database targets.",
        )

    def configuration_detail(self, snapshot: ConfigurationSnapshot) -> KeyValueView:
        return KeyValueView(
            title="Configuration source",
            rows=(
                ("path", str(snapshot.path)),
                ("ownership", snapshot.ownership.mode.value),
                ("source", snapshot.ownership.source_label),
                ("guidance", snapshot.ownership.guidance),
            ),
        )


def _target_row(target: DatabaseTarget, result: ConnectionResult | None) -> TableRow:
    if result is None:
        status = "Not tested"
        latency = ""
        detail = (detail_row("unknown", f"Connection not tested: {target.safe_url}"),)
    elif result.connected:
        status = "Warnings" if result.has_warnings else "Connected"
        latency = f"{result.latency_ms:.1f} ms" if result.latency_ms is not None else ""
        detail = _success_detail(result)
    else:
        status = _failure_status(result.failure)
        latency = ""
        detail = _failure_detail(result)
    return TableRow(
        key=target.key,
        cells=(
            target.label,
            target.connection_name,
            f"{target.cdm_schema} / {target.vocabulary_schema}",
            status,
            latency,
        ),
        detail=detail,
    )


def _database_actions(selected_target_key: str | None) -> tuple[ViewAction, ...]:
    return (
        ViewAction(
            "database.configure",
            "Configure",
            variant="primary",
            disabled=selected_target_key == "database.groundworkers",
        ),
        ViewAction(
            "database.test_connections",
            "Test connections",
        ),
        ViewAction("database.refresh", "Refresh"),
    )


def _configuration_status(snapshot: ConfigurationSnapshot) -> SemanticStatus:
    if snapshot.state is ConfigurationState.UNVERIFIED:
        return SemanticStatus.OK
    if snapshot.state is ConfigurationState.MISSING:
        return SemanticStatus.WARNING
    return SemanticStatus.ERROR


def _connection_status(results: Sequence[ConnectionResult]) -> SemanticStatus:
    if not results:
        return SemanticStatus.WARNING
    if not all(item.connected for item in results):
        return SemanticStatus.ERROR
    if any(item.has_warnings for item in results):
        return SemanticStatus.WARNING
    return SemanticStatus.OK


def _failure_status(failure: ClassifiedFailure | None) -> str:
    if failure is None:
        return "Failed"
    return failure.kind.value.replace("_", " ").title()


def _success_detail(result: ConnectionResult) -> DetailRows:
    detail = [detail_row("ok", f"Connected: {result.safe_url}")]
    if not result.diagnostics:
        detail.append(detail_row("ok", "Read-only connection check succeeded."))
        return tuple(detail)
    for index, diagnostic in enumerate(result.diagnostics, start=1):
        marker = "warn" if diagnostic.severity is DiagnosticSeverity.WARNING else "ok"
        detail.append(detail_row(marker, diagnostic.message))
    return tuple(detail)


def _failure_detail(result: ConnectionResult) -> DetailRows:
    detail = [detail_row("fail", f"Connection failed: {result.safe_url}")]
    if result.failure is None:
        detail.append(detail_row("fail", "Connection failed."))
        detail.append(detail_row("unknown", "Review logs."))
        return tuple(detail)
    detail.append(detail_row("fail", result.failure.detail))
    detail.append(detail_row("unknown", result.failure.next_action))
    return tuple(detail)
