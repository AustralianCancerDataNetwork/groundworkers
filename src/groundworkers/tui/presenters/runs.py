from __future__ import annotations

from groundskeeping.contracts import (
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    ViewAction,
)

from groundworkers.application.setup.maintenance_runs import (
    MaintenanceRun,
    MaintenanceRunStore,
    RunStatus,
)
from groundworkers.tui.presenters.base import SetupPresenterBase


class RunsPresenter(SetupPresenterBase):
    """Present the durable local maintenance history and safe controls."""

    def __init__(self, store: MaintenanceRunStore | None = None) -> None:
        self._store = store or MaintenanceRunStore()

    @property
    def store(self) -> MaintenanceRunStore:
        return self._store

    def status(self) -> SemanticStatus:
        runs = self._store.list()
        if any(run.status in {RunStatus.FAILED, RunStatus.INTERRUPTED} for run in runs):
            return SemanticStatus.ERROR
        if any(run.status in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.CANCELLING} for run in runs):
            return SemanticStatus.OK
        return SemanticStatus.IDLE

    def landing(self, *, selected_run_id: str | None = None) -> SurfaceView:
        runs = self._store.list()
        if not runs:
            return EmptyView(
                title="No maintenance runs",
                message="Ordered graph and embedding work will appear here and survive a TUI restart.",
                status=SemanticStatus.IDLE,
                actions=(ViewAction("runs.refresh", "Refresh"),),
            )
        return TableView(
            title="Maintenance runs",
            columns=("Kind", "Status", "Progress", "Message"),
            rows=tuple(_run_row(run, selected_run_id=selected_run_id) for run in runs),
            status=self.status(),
            actions=(
                ViewAction("runs.refresh", "Refresh"),
                ViewAction("runs.cancel", "Cancel selected"),
                ViewAction("runs.retry", "Retry selected"),
                ViewAction("runs.postflight", "Rerun postflight"),
                ViewAction("runs.export", "Export commands"),
            ),
            message="Run records and logs are stored in the local Groundworkers state directory.",
        )


def _run_row(run: MaintenanceRun, *, selected_run_id: str | None) -> TableRow:
    marker = "* " if run.run_id == selected_run_id else ""
    return TableRow(
        key=run.run_id,
        cells=(
            marker + run.kind,
            run.status.value,
            f"{run.completed}/{run.total}",
            run.last_message or "",
        ),
        detail=(
            ("run_id", run.run_id),
            ("status", run.status.value),
            ("progress", f"{run.completed}/{run.total}"),
            ("log", str(run.log_paths[0]) if run.log_paths else ""),
            ("message", run.last_message or ""),
        ),
    )


__all__ = ["RunsPresenter"]
