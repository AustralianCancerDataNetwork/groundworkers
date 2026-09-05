from __future__ import annotations

import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from groundskeeping.contracts import (
    Command,
    CommandPlan,
    CommandStep,
)

from groundworkers.application.setup.maintenance_runs import (
    MaintenanceRunner,
    MaintenanceRunStore,
    ResourceBusyError,
    RunStatus,
    StepStatus,
    run_plan_resources,
    safe_command_display,
)
from groundworkers.tui.presenters.runs import RunsPresenter


def _command(*code: str) -> Command:
    return Command(argv=(sys.executable, "-c", *code))


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
    plan = CommandPlan(
        kind="test",
        steps=(
            CommandStep("one", _command("print('one')"), affected_resources=("cdm:x",)),
            CommandStep("two", _command("print('two')"), affected_resources=("cdm:x",)),
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


def test_store_tail_log_delegates_to_bounded_shared_reader(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    run = store.create(
        CommandPlan(
            kind="tail",
            steps=(CommandStep("one", _command("pass")),),
        )
    )
    path = run.root / "step.log"
    path.write_text("0123456789", encoding="utf-8")
    store.save(replace(run, steps=(replace(run.steps[0], log_path=path),)))

    assert store.tail_log(run.run_id, max_bytes=4) == "6789"


def test_failed_run_retries_from_first_incomplete_step(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    plan = CommandPlan(
        kind="retry",
        steps=(
            CommandStep("done", _command("print('done')")),
            CommandStep("bad", _command("raise SystemExit(3)")),
        ),
    )

    created = MaintenanceRunner(store).start(
        plan,
        postflight=(_command("print('verified')"),),
    )
    finished = _wait(store, created.run_id)
    retry = store.retry(created.run_id)

    assert finished.status is RunStatus.FAILED
    assert finished.steps[0].status is StepStatus.SUCCEEDED
    assert [step.spec.key for step in retry.steps] == ["bad"]
    assert retry.retry_of == created.run_id


def test_runner_retry_preserves_lineage_on_the_started_record(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    created = MaintenanceRunner(store).start(
        CommandPlan(
            kind="retry-lineage",
            steps=(CommandStep("bad", _command("raise SystemExit(3)")),),
        )
    )
    _wait(store, created.run_id)

    retry = MaintenanceRunner(store).retry(created.run_id)

    assert retry.retry_of == created.run_id
    assert store.get(retry.run_id, recover=False).retry_of == created.run_id


def test_postflight_is_persisted_and_can_be_rerun(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    plan = CommandPlan(
        kind="postflight",
        steps=(CommandStep("work", _command("pass")),),
    )

    created = MaintenanceRunner(store).start(
        plan,
        postflight=(_command("print('verified')"),),
    )
    finished = _wait(store, created.run_id)
    rerun = MaintenanceRunner(store).rerun_postflight(created.run_id)
    rerun_finished = _wait(store, rerun.run_id)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.postflight_status is StepStatus.SUCCEEDED
    assert rerun_finished.status is RunStatus.SUCCEEDED


def test_failed_postflight_persists_its_generated_log_path(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    created = MaintenanceRunner(store).start(
        CommandPlan(
            kind="postflight-log",
            steps=(CommandStep("work", _command("pass")),),
        ),
        postflight=(_command("raise SystemExit(4)"),),
    )

    finished = _wait(store, created.run_id)

    assert finished.status is RunStatus.FAILED
    assert finished.postflight_status is StepStatus.FAILED
    assert len(finished.postflight_log_paths) == 1
    assert finished.postflight_log_paths[0].is_file()


def test_plan_level_resources_are_persisted_and_used_for_locking(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    plan = CommandPlan(
        kind="plan-resource",
        steps=(CommandStep("one", _command("pass")),),
        affected_resources=("db:plan",),
    )

    run = store.create(plan)
    conflicting = store.create(plan)

    assert run.affected_resources == ("db:plan",)
    assert run_plan_resources(store.get(run.run_id)) == ("db:plan",)

    locks = store.acquire(run, run_plan_resources(run))
    with pytest.raises(ResourceBusyError):
        store.acquire(conflicting, run_plan_resources(conflicting))
    store.release(run, locks)

    failed = store.save(replace(run, status=RunStatus.FAILED))
    retry = store.retry(failed.run_id)

    assert retry.affected_resources == ("db:plan",)


def test_resource_lock_blocks_active_conflict_and_releases(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    first = store.create(
        CommandPlan(kind="one", steps=(CommandStep("one", _command("pass"), ("db:x",)),))
    )
    second = store.create(
        CommandPlan(kind="two", steps=(CommandStep("two", _command("pass"), ("db:x",)),))
    )
    locks = store.acquire(first, ("db:x",))

    with pytest.raises(ResourceBusyError):
        store.acquire(second, ("db:x",))

    store.release(first, locks)
    released = store.acquire(second, ("db:x",))
    store.release(second, released)


def test_interrupted_supervisor_is_recovered_on_reopen(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    run = store.create(CommandPlan(kind="interrupted", steps=(CommandStep("one", _command("pass")),)))
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


def test_orphaned_pending_run_is_recovered_after_launch_grace(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    run = store.create(
        CommandPlan(
            kind="orphaned-pending",
            steps=(CommandStep("one", _command("pass")),),
        )
    )
    run = store.save(
        replace(
            run,
            created_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        )
    )

    recovered = store.get(run.run_id)

    assert recovered.status is RunStatus.INTERRUPTED


def test_pending_cancellation_is_terminal(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    run = store.create(
        CommandPlan(
            kind="pending-cancel",
            steps=(CommandStep("one", _command("pass")),),
        )
    )

    cancelled = store.cancel(run.run_id)

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.finished_at is not None


def test_command_display_redacts_credentials() -> None:
    command = Command(
        argv=("tool", "postgresql://user:password@example/db"),
        environment=(("API_TOKEN", "secret"), ("OA_CONFIG_PATH", "/tmp/config.toml")),
    )

    display = safe_command_display(command)

    assert "password" not in display
    assert "API_TOKEN=***" in display
    assert "OA_CONFIG_PATH=/tmp/config.toml" in display


def test_secret_environment_keys_are_rejected_before_persistence(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    command = Command(
        argv=("tool",),
        environment=(("API_TOKEN", "secret"),),
    )

    with pytest.raises(ValueError, match="secret-bearing"):
        store.create(CommandPlan(kind="secret", steps=(CommandStep("one", command),)))


def test_state_files_are_private_and_corrupt_records_do_not_hide_valid_runs(
    tmp_path: Path,
) -> None:
    store = MaintenanceRunStore(tmp_path)
    valid = store.create(
        CommandPlan(
            kind="valid",
            steps=(CommandStep("one", _command("pass")),),
        )
    )
    corrupt = store.root / "corrupt"
    corrupt.mkdir(mode=0o700)
    (corrupt / "run.json").write_text("{partial", encoding="utf-8")

    listed = store.list()

    assert [run.run_id for run in listed] == [valid.run_id]
    assert (valid.root.stat().st_mode & 0o777) == 0o700
    assert ((valid.root / "run.json").stat().st_mode & 0o777) == 0o600


def test_run_controls_match_the_selected_plan_and_state(tmp_path: Path) -> None:
    store = MaintenanceRunStore(tmp_path)
    run = store.create(
        CommandPlan(
            kind="no-postflight",
            steps=(CommandStep("one", _command("pass")),),
        )
    )
    view = RunsPresenter(store).landing(selected_run_id=run.run_id)
    actions = {action.key: action for action in view.actions}

    assert actions["runs.cancel"].disabled is False
    assert actions["runs.retry"].disabled is True
    assert actions["runs.postflight"].disabled is True
    assert actions["runs.export"].disabled is False


def test_selected_log_step_falls_back_while_current_step_starts(
    tmp_path: Path,
) -> None:
    from groundworkers.tui.pages.setup import _selected_log_step

    store = MaintenanceRunStore(tmp_path)
    run = store.create(
        CommandPlan(
            kind="starting-step",
            steps=(
                CommandStep("one", _command("pass")),
                CommandStep("two", _command("pass")),
            ),
        )
    )
    first_log = run.root / "step-01.log"
    first_log.write_text("earlier output", encoding="utf-8")
    current = replace(
        run,
        current_step=1,
        steps=(
            replace(run.steps[0], log_path=first_log),
            replace(run.steps[1], status=StepStatus.RUNNING),
        ),
    )

    assert _selected_log_step(current) == 0
