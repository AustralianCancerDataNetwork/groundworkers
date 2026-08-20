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
                message="Graph and embedding maintenance runs will appear here.",
                status=SemanticStatus.IDLE,
                actions=(ViewAction("runs.refresh", "Refresh"),),
            )
        selected = next(
            (run for run in runs if run.run_id == selected_run_id),
            None,
        )
        active = selected is not None and selected.status in {
            RunStatus.PENDING,
            RunStatus.RUNNING,
        }
        retryable = selected is not None and selected.status in {
            RunStatus.FAILED,
            RunStatus.INTERRUPTED,
            RunStatus.CANCELLED,
        }
        has_postflight = selected is not None and bool(selected.postflight)
        return TableView(
            title="Maintenance runs",
            columns=("Kind", "Status", "Progress", "Message"),
            rows=tuple(_run_row(run, selected_run_id=selected_run_id) for run in runs),
            status=self.status(),
            actions=(
                ViewAction("runs.refresh", "Refresh"),
                ViewAction("runs.cancel", "Cancel selected", disabled=not active),
                ViewAction("runs.retry", "Retry selected", disabled=not retryable),
                ViewAction(
                    "runs.postflight",
                    "Rerun postflight",
                    disabled=not has_postflight,
                ),
                ViewAction(
                    "runs.export",
                    "Export commands",
                    disabled=selected is None,
                ),
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
