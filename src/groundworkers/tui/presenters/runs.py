from __future__ import annotations

import re

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

_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-_][0-?]*[ -/]*[@-~])")
_TQDM_PROGRESS = re.compile(
    r"(?P<label>[^:\r\n]{1,80}):\s*"
    r"(?P<percent>\d+(?:\.\d+)?)%\s*"
    r"(?:\|.*?\|\s*)?"
    r"(?P<current>\d[\d,]*)\s*/\s*(?P<total>\d[\d,]*)"
    r"(?:\s*\[(?P<elapsed>[^<\]]+?)\s*<\s*"
    r"(?P<remaining>[^,\]]+?)(?:,\s*(?P<rate>[^\]]+))?\])?"
)


def format_progress_tail(tail: str, *, bar_width: int = 16) -> str:
    """Collapse repeated tqdm updates into one compact detail-pane display.

    Maintenance commands write their stdout and stderr to a durable log. This
    formatter is intentionally best-effort and Groundworkers-owned: it knows
    about the tqdm-like output produced by those commands, while non-matching
    lines remain available as ordinary log text.
    """
    cleaned = _ANSI_ESCAPE.sub("", tail).replace("\r", "\n")
    ordinary_lines: list[str] = []
    latest_progress: re.Match[str] | None = None
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _TQDM_PROGRESS.search(line)
        if match is None:
            ordinary_lines.append(line)
        else:
            latest_progress = match

    if latest_progress is None:
        return "\n".join(ordinary_lines)

    percent = max(0.0, min(100.0, float(latest_progress["percent"])))
    filled = round(bar_width * percent / 100)
    bar = "█" * filled + "░" * (bar_width - filled)
    percent_text = f"{percent:g}%"
    label = latest_progress["label"].strip()
    progress_lines = [f"{label}: [{bar}] {percent_text}"]
    counts = (
        f"{int(latest_progress['current'].replace(',', '')):,}/"
        f"{int(latest_progress['total'].replace(',', '')):,}"
    )
    metadata = [counts]
    if latest_progress["rate"]:
        metadata.append(latest_progress["rate"].strip())
    if latest_progress["remaining"]:
        metadata.append(f"ETA {latest_progress['remaining'].strip()}")
    if latest_progress["elapsed"]:
        metadata.append(f"elapsed {latest_progress['elapsed'].strip()}")
    progress_lines.append(" · ".join(metadata))
    return "\n".join((*ordinary_lines, *progress_lines))


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


__all__ = ["RunsPresenter", "format_progress_tail"]
