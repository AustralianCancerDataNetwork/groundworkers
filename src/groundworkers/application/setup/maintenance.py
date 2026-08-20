"""Launching a sibling package's maintenance CLI as a detached subprocess.

Groundworkers does not perform schema maintenance itself. The work belongs to
omop-alchemy and omop-graph, which expose it as CLI commands, and running those
out-of-process keeps slow DDL -- index builds, CLUSTER, tsvector population --
off the console's event loop and puts its output somewhere an operator can read
afterwards.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from groundworkers.application.setup.models import (
    MaintenanceCommand,
    MaintenanceLaunch,
)

__all__ = [
    "MaintenanceCommandError",
    "launch_maintenance_command",
    "run_maintenance_command",
]


class MaintenanceCommandError(RuntimeError):
    """A synchronous maintenance command exited unsuccessfully."""

    def __init__(self, launch: MaintenanceLaunch, returncode: int) -> None:
        self.launch = launch
        self.returncode = returncode
        super().__init__(
            f"Maintenance command exited with status {returncode}: "
            f"{launch.command.display}. Log: {launch.log_path}"
        )


def launch_maintenance_command(
    command: MaintenanceCommand,
    *,
    log_prefix: str,
    log_dir: str | Path = "/tmp",
) -> MaintenanceLaunch:
    """Start *command* detached and return immediately with its log path."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    log_path = Path(log_dir) / f"groundworkers-{log_prefix}-{stamp}-{uuid4().hex[:8]}.log"
    env = os.environ.copy()
    env.update(dict(command.environment))
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command.argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    return MaintenanceLaunch(command=command, pid=process.pid, log_path=log_path)


def run_maintenance_command(
    command: MaintenanceCommand,
    *,
    log_prefix: str,
    log_dir: str | Path = "/tmp",
) -> MaintenanceLaunch:
    """Run one maintenance command to completion with a unique log."""

    launch = launch_maintenance_command(
        command,
        log_prefix=log_prefix,
        log_dir=log_dir,
    )
    # The detached launcher intentionally returns immediately for population.
    # Graph preparation uses this synchronous boundary so the next prerequisite
    # cannot race the previous one.
    _, status = os.waitpid(launch.pid, 0)
    returncode = os.waitstatus_to_exitcode(status)
    if returncode != 0:
        raise MaintenanceCommandError(launch, returncode)
    return launch
