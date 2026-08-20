from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from groundworkers.application.setup.maintenance_runs import (
    MaintenancePlan,
    MaintenanceRunner,
    MaintenanceRunStore,
    MaintenanceStep,
    ResourceBusyError,
    RunStatus,
    StepStatus,
    safe_command_display,
)
from groundworkers.application.setup.models import MaintenanceCommand


def _command(*code: str) -> MaintenanceCommand:
    return MaintenanceCommand(argv=(sys.executable, "-c", *code))


def _wait(store: MaintenanceRunStore, run_id: str):
    for _ in range(200):
        run = store.get(run_id)
        if run.status not in {
            RunStatus.PENDING,
            RunStatus.RUNNING,
            RunStatus.CANCELLING,
        }:
            return run
        time.sleep(0.02)
    raise AssertionError("maintenance run did not finish")


def test_run_survives_store_reopen_and_keeps_ordered_logs(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    plan = MaintenancePlan(
        kind="test",
        steps=(
            MaintenanceStep("one", _command("print('one')"), affected_resources=("cdm:x",)),
            MaintenanceStep("two", _command("print('two')"), affected_resources=("cdm:x",)),
        ),
    )

    created = MaintenanceRunner(store).start(plan)
    finished = _wait(store, created.run_id)
    reopened = MaintenanceRunStore(tmp_path).get(created.run_id)

    assert finished.status is RunStatus.SUCCEEDED
    assert reopened.completed == 2
    assert [step.status for step in reopened.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.SUCCEEDED,
    ]
    assert reopened.log_paths[0].read_text(encoding="utf-8").strip() == "one"
    assert reopened.log_paths[1].read_text(encoding="utf-8").strip() == "two"


def test_failed_run_retries_from_first_incomplete_step(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    plan = MaintenancePlan(
        kind="retry",
        steps=(
            MaintenanceStep("done", _command("print('done')")),
            MaintenanceStep("bad", _command("raise SystemExit(3)")),
        ),
    )

    created = MaintenanceRunner(store).start(plan)
    finished = _wait(store, created.run_id)
    retry = store.retry(created.run_id)

    assert finished.status is RunStatus.FAILED
    assert finished.steps[0].status is StepStatus.SUCCEEDED
    assert [step.spec.key for step in retry.steps] == ["bad"]
    assert retry.retry_of == created.run_id


def test_postflight_is_persisted_and_can_be_rerun(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    plan = MaintenancePlan(
        kind="postflight",
        steps=(MaintenanceStep("work", _command("pass")),),
        postflight=(_command("print('verified')"),),
    )

    created = MaintenanceRunner(store).start(plan)
    finished = _wait(store, created.run_id)
    rerun = MaintenanceRunner(store).rerun_postflight(created.run_id)
    rerun_finished = _wait(store, rerun.run_id)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.postflight_status is StepStatus.SUCCEEDED
    assert rerun_finished.status is RunStatus.SUCCEEDED


def test_resource_lock_blocks_active_conflict_and_releases(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    first = store.create(
        MaintenancePlan(kind="one", steps=(MaintenanceStep("one", _command("pass"), ("db:x",)),))
    )
    second = store.create(
        MaintenancePlan(kind="two", steps=(MaintenanceStep("two", _command("pass"), ("db:x",)),))
    )
    locks = store.acquire(first, ("db:x",))

    with pytest.raises(ResourceBusyError):
        store.acquire(second, ("db:x",))

    store.release(first, locks)
    released = store.acquire(second, ("db:x",))
    store.release(second, released)


def test_interrupted_supervisor_is_recovered_on_reopen(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    run = store.create(MaintenancePlan(kind="interrupted", steps=(MaintenanceStep("one", _command("pass")),)))
    run = store.save(
        run.__class__(
            **{
                **run.__dict__,
                "status": RunStatus.RUNNING,
                "supervisor_pid": 999999,
                "supervisor_start_time": 1.0,
            }
        )
    )

    recovered = MaintenanceRunStore(tmp_path).get(run.run_id)

    assert recovered.status is RunStatus.INTERRUPTED
    assert recovered.failure == "interrupted"


def test_command_display_redacts_credentials() -> None:
    command = MaintenanceCommand(
        argv=("tool", "postgresql://user:password@example/db"),
        environment=(("API_TOKEN", "secret"), ("OA_CONFIG_PATH", "/tmp/config.toml")),
    )

    display = safe_command_display(command)

    assert "password" not in display
    assert "secret" not in display
    assert "OA_CONFIG_PATH=/tmp/config.toml" in display
