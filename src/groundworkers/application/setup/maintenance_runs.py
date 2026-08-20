"""Durable local maintenance runs for the setup surface.

This is deliberately a local operator boundary.  MCP and REST remain stateless;
the run directory is only for work started by the optional setup console.  A
small supervisor process owns execution so closing the TUI does not orphan the
record or lose the result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

import psutil

from groundworkers.application.setup.models import MaintenanceCommand


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class MaintenanceStep:
    """One ordered, idempotent maintenance command."""

    key: str
    command: MaintenanceCommand
    affected_resources: tuple[str, ...] = ()
    idempotent: bool = True


@dataclass(frozen=True)
class MaintenancePlan:
    """A serialisable ordered plan owned by the local run store."""

    kind: str
    steps: tuple[MaintenanceStep, ...]
    affected_resources: tuple[str, ...] = ()
    postflight: tuple[MaintenanceCommand, ...] = ()
    unit: str = "step"

    @property
    def resources(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (*self.affected_resources, *(resource for step in self.steps for resource in step.affected_resources))
            )
        )


@dataclass(frozen=True)
class MaintenanceStepRecord:
    spec: MaintenanceStep
    status: StepStatus = StepStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    log_path: Path | None = None
    process_pid: int | None = None
    process_start_time: float | None = None
    message: str | None = None


@dataclass(frozen=True)
class MaintenanceRun:
    run_id: str
    kind: str
    status: RunStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    root: Path
    steps: tuple[MaintenanceStepRecord, ...]
    postflight: tuple[MaintenanceCommand, ...] = ()
    postflight_status: StepStatus | None = None
    current_step: int | None = None
    completed: int = 0
    last_message: str | None = None
    exit_code: int | None = None
    supervisor_pid: int | None = None
    supervisor_start_time: float | None = None
    retry_of: str | None = None
    failure: str | None = None

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def log_paths(self) -> tuple[Path, ...]:
        return tuple(step.log_path for step in self.steps if step.log_path is not None)


class ResourceBusyError(RuntimeError):
    """A conflicting active maintenance run owns one of the requested resources."""


class MaintenanceRunStore:
    """Atomic JSON records and per-run logs under the platform state directory."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = resolve_state_root(root) / "runs"
        self.lock_root = self.root / "locks"

    def create(
        self,
        plan: MaintenancePlan,
        *,
        retry_of: str | None = None,
    ) -> MaintenanceRun:
        _validate_plan(plan)
        self.root.mkdir(parents=True, exist_ok=True)
        _ensure_private_directory(self.root)
        run_id = uuid4().hex
        run_root = self.root / run_id
        run_root.mkdir(mode=0o700)
        run = MaintenanceRun(
            run_id=run_id,
            kind=plan.kind,
            status=RunStatus.PENDING,
            created_at=_now(),
            started_at=None,
            finished_at=None,
            root=run_root,
            steps=tuple(MaintenanceStepRecord(step) for step in plan.steps),
            postflight=plan.postflight,
            retry_of=retry_of,
        )
        self.save(run)
        return run

    def get(self, run_id: str, *, recover: bool = True) -> MaintenanceRun:
        path = self.root / run_id / "run.json"
        if not path.is_file():
            raise KeyError(f"Unknown maintenance run: {run_id}")
        run = _run_from_json(json.loads(path.read_text(encoding="utf-8")), self.root / run_id)
        if recover and _run_needs_recovery(run):
            if not _process_matches(run.supervisor_pid, run.supervisor_start_time):
                run = self.save(
                    replace(
                        run,
                        status=RunStatus.INTERRUPTED,
                        finished_at=_now(),
                        last_message="Supervisor is no longer running; work was interrupted.",
                        failure="interrupted",
                        steps=tuple(
                            replace(
                                step,
                                status=(
                                    StepStatus.INTERRUPTED
                                    if step.status is StepStatus.RUNNING
                                    else step.status
                                ),
                                finished_at=(
                                    _now()
                                    if step.status is StepStatus.RUNNING
                                    else step.finished_at
                                ),
                            )
                            for step in run.steps
                        ),
                    )
                )
        return run

    def list(self, *, recover: bool = True) -> tuple[MaintenanceRun, ...]:
        if not self.root.is_dir():
            return ()
        runs = []
        for path in self.root.iterdir():
            if path.is_dir() and (path / "run.json").is_file():
                try:
                    runs.append(self.get(path.name, recover=recover))
                except (OSError, ValueError, KeyError):
                    continue
        return tuple(sorted(runs, key=lambda item: item.created_at, reverse=True))

    def save(self, run: MaintenanceRun) -> MaintenanceRun:
        run.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _ensure_private_directory(run.root)
        target = run.root / "run.json"
        temporary = run.root / f".run-{uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(_run_to_json(run), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
        return run

    def acquire(self, run: MaintenanceRun, resources: Iterable[str]) -> tuple[Path, ...]:
        self.lock_root.mkdir(parents=True, exist_ok=True)
        _ensure_private_directory(self.lock_root)
        acquired: list[Path] = []
        try:
            for resource in dict.fromkeys(resources):
                lock_path = self.lock_root / _resource_filename(resource)
                try:
                    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    owner = _read_lock(lock_path)
                    if owner and owner != run.run_id and self._owner_is_active(owner):
                        raise ResourceBusyError(
                            f"Resource '{resource}' is already in use by maintenance run {owner}."
                        )
                    lock_path.unlink(missing_ok=True)
                    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(run.run_id)
                acquired.append(lock_path)
            return tuple(acquired)
        except Exception:
            self.release(run, acquired)
            raise

    def release(self, run: MaintenanceRun, locks: Iterable[Path]) -> None:
        for path in locks:
            if _read_lock(path) == run.run_id:
                path.unlink(missing_ok=True)

    def _owner_is_active(self, run_id: str) -> bool:
        try:
            return self.get(run_id).status in {
                RunStatus.PENDING,
                RunStatus.RUNNING,
                RunStatus.CANCELLING,
            }
        except KeyError:
            return False

    def cancel(self, run_id: str) -> MaintenanceRun:
        run = self.get(run_id)
        if run.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
            return run
        was_pending = run.status is RunStatus.PENDING
        run = self.save(
            replace(run, status=RunStatus.CANCELLING, last_message="Cancellation requested.")
        )
        if run.current_step is not None:
            current = run.steps[run.current_step]
            if _process_matches(current.process_pid, current.process_start_time):
                _terminate_process_group(current.process_pid, current.process_start_time)
        if _process_matches(run.supervisor_pid, run.supervisor_start_time):
            _terminate_process_group(run.supervisor_pid, run.supervisor_start_time)
        if was_pending:
            return self.save(
                replace(
                    run,
                    status=RunStatus.CANCELLED,
                    finished_at=_now(),
                    last_message="Cancelled before execution started.",
                )
            )
        return run

    def retry(self, run_id: str) -> MaintenanceRun:
        previous = self.get(run_id)
        if previous.status not in {RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.CANCELLED}:
            raise ValueError("Only failed, interrupted, or cancelled runs can be retried.")
        first_incomplete = next(
            (index for index, step in enumerate(previous.steps) if step.status is not StepStatus.SUCCEEDED),
            len(previous.steps),
        )
        remaining = previous.steps[first_incomplete:]
        if any(not step.spec.idempotent for step in remaining):
            raise ValueError("The first incomplete step is not safe to retry.")
        plan = MaintenancePlan(
            kind=previous.kind,
            steps=tuple(step.spec for step in remaining),
            postflight=previous.postflight,
        )
        return self.create(plan, retry_of=run_id)

    def tail_log(
        self,
        run_id: str,
        *,
        step: int = 0,
        max_bytes: int = 8192,
    ) -> str:
        """Read a bounded tail for the Runs surface without loading a whole log."""

        run = self.get(run_id)
        if step < 0 or step >= len(run.steps):
            raise IndexError(f"Unknown maintenance step: {step}")
        path = run.steps[step].log_path
        if path is None:
            return ""
        if not path.is_file():
            return ""
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - max_bytes))
            return handle.read().decode("utf-8", errors="replace")

    def export_commands(self, run_id: str) -> tuple[str, ...]:
        """Return safe command displays for manual troubleshooting/export."""

        run = self.get(run_id, recover=False)
        return tuple(safe_command_display(step.spec.command) for step in run.steps)


class MaintenanceRunner:
    """Start and supervise durable maintenance plans."""

    def __init__(self, store: MaintenanceRunStore | None = None) -> None:
        self.store = store or MaintenanceRunStore()

    def start(
        self,
        plan: MaintenancePlan,
        *,
        retry_of: str | None = None,
    ) -> MaintenanceRun:
        run = self.store.create(plan, retry_of=retry_of)
        return self._start_created(run)

    def _start_created(self, run: MaintenanceRun) -> MaintenanceRun:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "groundworkers.application.setup.maintenance_runs",
                "--state-root",
                str(self.store.root.parent),
                "--run-id",
                run.run_id,
            ],
            start_new_session=True,
            close_fds=True,
        )
        current = self.store.get(run.run_id, recover=False)
        return self.store.save(
            replace(
                current,
                supervisor_pid=process.pid,
                supervisor_start_time=_process_start_time(process.pid),
            )
        )

    def execute(self, run_id: str) -> MaintenanceRun:
        return _execute_run(self.store, run_id)

    def rerun_postflight(self, run_id: str) -> MaintenanceRun:
        """Run only the persisted postflight checks as a new local run."""

        previous = self.store.get(run_id)
        if not previous.postflight:
            raise ValueError("This maintenance run has no postflight verification plan.")
        return self.start(
            MaintenancePlan(
                kind=f"{previous.kind}-postflight",
                steps=tuple(
                    MaintenanceStep(
                        key=f"postflight-{index}",
                        command=command,
                        affected_resources=run_plan_resources(previous),
                    )
                    for index, command in enumerate(previous.postflight, start=1)
                ),
            )
        )

    def retry(self, run_id: str) -> MaintenanceRun:
        """Create and start a retry from the first incomplete safe step."""

        pending = self.store.retry(run_id)
        # ``store.retry`` owns the exact safe-resume calculation and persists
        # the lineage. Start that record directly instead of deleting it and
        # creating an unrelated run.
        return self._start_created(pending)


_ACTIVE_CHILD: subprocess.Popen[bytes] | None = None
_CANCEL_REQUESTED = False


def _execute_run(store: MaintenanceRunStore, run_id: str) -> MaintenanceRun:
    global _ACTIVE_CHILD, _CANCEL_REQUESTED
    run = store.get(run_id, recover=False)
    # The supervisor records its own identity before publishing RUNNING. This
    # closes the launch race where the child could become live before the
    # parent had attached the PID, causing a reopening TUI to "recover" active
    # work as interrupted.
    run = store.save(
        replace(
            run,
            supervisor_pid=os.getpid(),
            supervisor_start_time=_process_start_time(os.getpid()),
        )
    )
    locks: tuple[Path, ...] = ()
    _CANCEL_REQUESTED = False

    def request_cancel(_signum, _frame) -> None:
        global _CANCEL_REQUESTED
        _CANCEL_REQUESTED = True
        if _ACTIVE_CHILD is not None:
            _terminate_process_group(_ACTIVE_CHILD.pid)

    previous_handler = signal.signal(signal.SIGTERM, request_cancel)
    try:
        if run.status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
            return store.save(
                replace(run, status=RunStatus.CANCELLED, finished_at=_now(), last_message="Cancelled before execution started.")
            )
        try:
            locks = store.acquire(run, run_plan_resources(run))
        except ResourceBusyError as exc:
            return store.save(
                replace(run, status=RunStatus.FAILED, finished_at=_now(), failure=str(exc), last_message=str(exc))
            )
        run = store.save(replace(run, status=RunStatus.RUNNING, started_at=_now(), last_message="Run started."))
        for index, step in enumerate(run.steps):
            if _CANCEL_REQUESTED:
                return _finish_cancelled(store, run, index)
            log_path = run.root / f"step-{index + 1:02d}-{_safe_key(step.spec.key)}.log"
            started = _now()
            record = replace(
                step,
                status=StepStatus.RUNNING,
                started_at=started,
                log_path=log_path,
                message=f"Running {step.spec.key}.",
            )
            run = store.save(
                replace(
                    run,
                    status=RunStatus.RUNNING,
                    current_step=index,
                    last_message=record.message,
                    steps=(*run.steps[:index], record, *run.steps[index + 1 :]),
                )
            )
            returncode, pid, start_time = _run_command(step.spec.command, log_path)
            _ACTIVE_CHILD = None
            finished = _now()
            status = StepStatus.CANCELLED if _CANCEL_REQUESTED else (
                StepStatus.SUCCEEDED if returncode == 0 else StepStatus.FAILED
            )
            record = replace(
                record,
                status=status,
                finished_at=finished,
                exit_code=returncode,
                process_pid=pid,
                process_start_time=start_time,
                message=(
                    "Cancelled."
                    if status is StepStatus.CANCELLED
                    else ("Completed." if status is StepStatus.SUCCEEDED else f"Exited with status {returncode}.")
                ),
            )
            run = store.save(
                replace(
                    run,
                    current_step=index,
                    completed=index + (1 if status is StepStatus.SUCCEEDED else 0),
                    last_message=record.message,
                    steps=(*run.steps[:index], record, *run.steps[index + 1 :]),
                )
            )
            if status is not StepStatus.SUCCEEDED:
                return store.save(
                    replace(
                        run,
                        status=RunStatus.CANCELLED if status is StepStatus.CANCELLED else RunStatus.FAILED,
                        finished_at=_now(),
                        exit_code=returncode,
                        failure=record.message,
                    )
                )
        run = store.save(replace(run, current_step=None, last_message="Steps complete."))
        if run.postflight:
            run = store.save(replace(run, postflight_status=StepStatus.RUNNING, last_message="Running postflight verification."))
            for index, command in enumerate(run.postflight, start=1):
                code, pid, _ = _run_command(command, run.root / f"postflight-{index:02d}.log")
                if code != 0:
                    return store.save(
                        replace(
                            run,
                            status=RunStatus.FAILED,
                            finished_at=_now(),
                            exit_code=code,
                            postflight_status=StepStatus.FAILED,
                            failure=f"Postflight step {index} exited with status {code}.",
                            last_message=f"Postflight step {index} failed (PID {pid}).",
                        )
                    )
            run = store.save(replace(run, postflight_status=StepStatus.SUCCEEDED))
        return store.save(replace(run, status=RunStatus.SUCCEEDED, finished_at=_now(), completed=run.total, last_message="Run completed."))
    except Exception as exc:
        return store.save(replace(run, status=RunStatus.INTERRUPTED, finished_at=_now(), failure=str(exc), last_message="Run interrupted."))
    finally:
        store.release(run, locks)
        signal.signal(signal.SIGTERM, previous_handler)


def _run_command(command: MaintenanceCommand, log_path: Path) -> tuple[int, int | None, float | None]:
    global _ACTIVE_CHILD
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(dict(command.environment))
    with log_path.open("ab") as log_file:
        log_path.chmod(0o600)
        process = subprocess.Popen(
            command.argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        _ACTIVE_CHILD = process
        start_time = _process_start_time(process.pid)
        returncode = process.wait()
    return returncode, process.pid, start_time


def _finish_cancelled(store: MaintenanceRunStore, run: MaintenanceRun, index: int) -> MaintenanceRun:
    steps = list(run.steps)
    if index < len(steps) and steps[index].status is StepStatus.RUNNING:
        steps[index] = replace(steps[index], status=StepStatus.CANCELLED, finished_at=_now())
    return store.save(
        replace(run, status=RunStatus.CANCELLED, finished_at=_now(), current_step=None, steps=tuple(steps), last_message="Cancelled before the next step.")
    )


def run_plan_resources(run: MaintenanceRun) -> tuple[str, ...]:
    return tuple(dict.fromkeys(resource for step in run.steps for resource in step.spec.affected_resources))


def resolve_state_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    configured = os.getenv("GROUNDWORKERS_STATE_HOME") or os.getenv("XDG_STATE_HOME")
    return Path(configured).expanduser() / "groundworkers" if configured else Path.home() / ".local" / "state" / "groundworkers"


_PENDING_RECOVERY_GRACE = timedelta(seconds=5)


def _run_needs_recovery(run: MaintenanceRun) -> bool:
    if run.status in {RunStatus.RUNNING, RunStatus.CANCELLING}:
        return True
    if run.status is not RunStatus.PENDING:
        return False
    if run.supervisor_pid is not None:
        return True
    try:
        created = datetime.fromisoformat(run.created_at)
    except ValueError:
        return True
    return datetime.now(UTC) - created >= _PENDING_RECOVERY_GRACE


def _ensure_private_directory(path: Path) -> None:
    """Keep local run metadata private even under a permissive process umask."""

    path.chmod(0o700)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _process_start_time(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError, ValueError):
        return None


def _process_matches(pid: int | None, start_time: float | None) -> bool:
    if pid is None or start_time is None:
        return False
    current = _process_start_time(pid)
    return current is not None and abs(current - start_time) < 0.01


def _terminate_process_group(pid: int | None, expected_start_time: float | None = None) -> None:
    if pid is None:
        return
    expected_start_time = expected_start_time if expected_start_time is not None else _process_start_time(pid)
    if not _process_matches(pid, expected_start_time):
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _resource_filename(resource: str) -> str:
    return hashlib.sha256(resource.encode("utf-8")).hexdigest() + ".lock"


def _read_lock(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "step"


def _redact(value: str) -> str:
    value = re.sub(r"(?i)(://[^/:@]+):[^/@]+@", r"\1:***@", value)
    return value


def safe_command_display(command: MaintenanceCommand) -> str:
    """Return a persisted command display with environment secrets removed."""

    parts: list[str] = []
    for key, value in command.environment:
        shown = "***" if re.search(r"(?i)(key|token|secret|password|credential)", key) else _redact(value)
        parts.append(f"{key}={shown}")
    parts.extend(_redact(part) for part in command.argv)
    return " ".join(parts)


def _validate_plan(plan: MaintenancePlan) -> None:
    """Reject command inputs that would persist obvious credentials."""

    commands = (*tuple(step.command for step in plan.steps), *plan.postflight)
    for command in commands:
        for key, _value in command.environment:
            if re.search(r"(?i)(key|token|secret|password|credential)", key):
                raise ValueError(
                    f"Maintenance command environment '{key}' is secret-bearing; "
                    "pass a secret reference instead."
                )
        for argument in command.argv:
            if re.search(r"(?i)://[^/:@]+:[^/@]+@", argument):
                raise ValueError("Maintenance command contains a credential-bearing URL.")


def _command_to_json(command: MaintenanceCommand) -> dict[str, object]:
    return {"argv": list(command.argv), "environment": [[key, _redact(value)] for key, value in command.environment], "display": safe_command_display(command)}


def _step_to_json(record: MaintenanceStepRecord, root: Path) -> dict[str, object]:
    return {
        "key": record.spec.key,
        "command": _command_to_json(record.spec.command),
        "affected_resources": list(record.spec.affected_resources),
        "idempotent": record.spec.idempotent,
        "status": record.status.value,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "exit_code": record.exit_code,
        "log_path": str(record.log_path.relative_to(root)) if record.log_path else None,
        "process_pid": record.process_pid,
        "process_start_time": record.process_start_time,
        "message": record.message,
    }


def _run_to_json(run: MaintenanceRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "kind": run.kind,
        "status": run.status.value,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "current_step": run.current_step,
        "completed": run.completed,
        "last_message": run.last_message,
        "exit_code": run.exit_code,
        "supervisor_pid": run.supervisor_pid,
        "supervisor_start_time": run.supervisor_start_time,
        "retry_of": run.retry_of,
        "failure": run.failure,
        "postflight_status": run.postflight_status.value if run.postflight_status else None,
        "postflight": [_command_to_json(command) for command in run.postflight],
        "steps": [_step_to_json(step, run.root) for step in run.steps],
    }


def _command_from_json(data: dict[str, object]) -> MaintenanceCommand:
    return MaintenanceCommand(
        argv=tuple(str(value) for value in data.get("argv", ())),
        environment=tuple((str(pair[0]), str(pair[1])) for pair in data.get("environment", ())),
    )


def _run_from_json(data: dict[str, object], root: Path) -> MaintenanceRun:
    steps = []
    for item in data.get("steps", ()):
        item = dict(item)
        log = item.get("log_path")
        steps.append(
            MaintenanceStepRecord(
                spec=MaintenanceStep(
                    key=str(item["key"]),
                    command=_command_from_json(dict(item["command"])),
                    affected_resources=tuple(str(value) for value in item.get("affected_resources", ())),
                    idempotent=bool(item.get("idempotent", True)),
                ),
                status=StepStatus(str(item.get("status", StepStatus.PENDING.value))),
                started_at=item.get("started_at"),
                finished_at=item.get("finished_at"),
                exit_code=item.get("exit_code"),
                log_path=(root / str(log)) if log else None,
                process_pid=item.get("process_pid"),
                process_start_time=item.get("process_start_time"),
                message=item.get("message"),
            )
        )
    return MaintenanceRun(
        run_id=str(data["run_id"]),
        kind=str(data["kind"]),
        status=RunStatus(str(data["status"])),
        created_at=str(data["created_at"]),
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
        root=root,
        steps=tuple(steps),
        postflight=tuple(_command_from_json(dict(command)) for command in data.get("postflight", ())),
        postflight_status=(StepStatus(str(data["postflight_status"])) if data.get("postflight_status") else None),
        current_step=data.get("current_step"),
        completed=int(data.get("completed", 0)),
        last_message=data.get("last_message"),
        exit_code=data.get("exit_code"),
        supervisor_pid=data.get("supervisor_pid"),
        supervisor_start_time=data.get("supervisor_start_time"),
        retry_of=data.get("retry_of"),
        failure=data.get("failure"),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one Groundworkers maintenance plan")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    MaintenanceRunner(MaintenanceRunStore(args.state_root)).execute(args.run_id)


if __name__ == "__main__":
    main()


__all__ = [
    "MaintenancePlan",
    "MaintenanceRun",
    "MaintenanceRunStore",
    "MaintenanceRunner",
    "MaintenanceStep",
    "MaintenanceStepRecord",
    "ResourceBusyError",
    "RunStatus",
    "StepStatus",
    "resolve_state_root",
    "safe_command_display",
]
