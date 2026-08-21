from __future__ import annotations

import sys
from pathlib import Path

import pytest

from groundworkers.application.setup import graph_maintenance
from groundworkers.application.setup.maintenance import (
    MaintenanceCommandError,
    launch_maintenance_command,
    run_maintenance_command,
)
from groundworkers.application.setup.models import MaintenanceCommand, MaintenanceLaunch


def _command(*code: str) -> MaintenanceCommand:
    return MaintenanceCommand(argv=(sys.executable, "-c", *code))


def test_maintenance_logs_are_unique_per_invocation(tmp_path: Path) -> None:
    first = launch_maintenance_command(
        _command("pass"), log_prefix="graph-1", log_dir=tmp_path
    )
    second = launch_maintenance_command(
        _command("pass"), log_prefix="graph-1", log_dir=tmp_path
    )

    assert first.log_path != second.log_path
    assert first.log_path.parent == tmp_path


def test_synchronous_runner_reports_nonzero_exit_and_log(tmp_path: Path) -> None:
    with pytest.raises(MaintenanceCommandError) as raised:
        run_maintenance_command(
            _command("raise SystemExit(7)"),
            log_prefix="graph-1",
            log_dir=tmp_path,
        )

    assert raised.value.returncode == 7
    assert raised.value.launch.log_path.is_file()


def test_graph_prerequisites_stop_after_the_first_failed_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands = (_command("pass"), _command("pass"), _command("pass"))
    calls: list[int] = []

    def run(command, *, log_prefix, log_dir):
        calls.append(int(log_prefix.rsplit("-", 1)[1]))
        launch = MaintenanceLaunch(
            command=command,
            pid=1000 + len(calls),
            log_path=tmp_path / f"{log_prefix}.log",
        )
        if len(calls) == 2:
            raise MaintenanceCommandError(launch, 9)
        return launch

    monkeypatch.setattr(graph_maintenance, "run_maintenance_command", run)

    with pytest.raises(RuntimeError, match="step 2 failed"):
        graph_maintenance.launch_graph_remediation(commands, log_dir=tmp_path)

    assert calls == [1, 2]
