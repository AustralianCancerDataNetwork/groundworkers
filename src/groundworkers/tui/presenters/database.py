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
    ConfigurationSnapshot,
    ConfigurationState,
    ConnectionResult,
    DatabaseTarget,
)


class DatabasePresenter:
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
    ) -> SurfaceView:
        if snapshot.state is ConfigurationState.MISSING:
            return EmptyView(
                title="No stack configuration found",
                message=(
                    "Create a database connection and map the CDM and vocabulary "
                    "schemas before continuing."
                ),
                status=SemanticStatus.WARNING,
                actions=(ViewAction("database.refresh", "Refresh"),),
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
                actions=(ViewAction("database.refresh", "Refresh"),),
            )

        result_by_key = {item.target_key: item for item in results}
        return TableView(
            title="Database resources",
            columns=("Resource", "Database", "Schemas", "Status", "Latency"),
            rows=tuple(
                _target_row(target, result_by_key.get(target.key)) for target in targets
            ),
            status=_connection_status(results),
            message=(
                f"{snapshot.path}  |  profile {snapshot.profile or 'default'}  |  "
                f"{snapshot.ownership.mode.value}"
            ),
            actions=(
                ViewAction(
                    "database.test_connections",
                    "Test connections",
                    variant="primary",
                ),
                ViewAction("database.refresh", "Refresh"),
            ),
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
                ("profile", snapshot.profile or "default"),
                ("ownership", snapshot.ownership.mode.value),
                ("source", snapshot.ownership.source_label),
                ("guidance", snapshot.ownership.guidance),
            ),
        )


def _target_row(target: DatabaseTarget, result: ConnectionResult | None) -> TableRow:
    if result is None:
        status = "Not tested"
        latency = ""
        detail = {"safe_url": target.safe_url}
    elif result.connected:
        status = "Connected"
        latency = f"{result.latency_ms:.1f} ms" if result.latency_ms is not None else ""
        detail = {"safe_url": result.safe_url}
    else:
        status = "Failed"
        latency = ""
        detail = {
            "safe_url": result.safe_url,
            "cause": result.failure.detail if result.failure else "Connection failed.",
            "next_action": result.failure.next_action
            if result.failure
            else "Review logs.",
        }
    return TableRow(
        key=target.key,
        cells=(
            target.label,
            target.database_name,
            f"{target.cdm_schema} / {target.vocabulary_schema}",
            status,
            latency,
        ),
        detail=detail,
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
    return (
        SemanticStatus.OK
        if all(item.connected for item in results)
        else SemanticStatus.ERROR
    )
