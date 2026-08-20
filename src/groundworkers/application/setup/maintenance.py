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

from groundworkers.application.setup.models import (
    MaintenanceCommand,
    MaintenanceLaunch,
)

__all__ = ["launch_maintenance_command"]


def launch_maintenance_command(
    command: MaintenanceCommand,
    *,
    log_prefix: str,
    log_dir: str | Path = "/tmp",
) -> MaintenanceLaunch:
    """Start *command* detached and return immediately with its log path."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = Path(log_dir) / f"groundworkers-{log_prefix}-{stamp}.log"
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
