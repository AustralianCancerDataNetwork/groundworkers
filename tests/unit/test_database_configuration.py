from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.database_configuration import (
    ConnectionStrategy,
    DatabaseConfigurationDraft,
    PasswordAction,
    apply_database_configuration,
    candidate_targets,
    draft_from_plan,
    plan_database_configuration,
)
from groundworkers.application.setup.databases import resolve_database_targets


def test_database_configuration_creates_stack_from_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    snapshot = load_configuration(config_path=path)
    draft = DatabaseConfigurationDraft(
        connection_strategy=ConnectionStrategy.CREATE,
        connection_name="local_sqlite",
        dialect="sqlite",
        database_name=":memory:",
        cdm_schema="main",
        vocabulary_schema="vocab",
    )

    result = apply_database_configuration(snapshot, draft)

    assert result.save_result.replaced_existing is False
    reloaded = load_configuration(config_path=path)
    targets = resolve_database_targets(reloaded)
    assert targets[0].database_name == "local_sqlite"
    assert targets[0].safe_url == "sqlite:///:memory:"
    assert "groundworkers" in reloaded.stack.tools


def test_blank_password_preserves_existing_secret(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[databases.main]
dialect = "postgresql+psycopg"
host = "db.example"
user = "demo"
password = "old-secret"
database_name = "omop"

[resources.cdm_db]
database = "main"
cdm_schema = "cdm"
""",
    )
    snapshot = load_configuration(config_path=path)
    draft = draft_from_plan(plan_database_configuration(snapshot), snapshot)

    apply_database_configuration(snapshot, draft)

    reloaded = load_configuration(config_path=path)
    assert reloaded.stack.databases["main"].password == "old-secret"


def test_explicit_clear_removes_existing_secret(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[databases.main]
dialect = "postgresql+psycopg"
host = "db.example"
user = "demo"
password = "old-secret"
database_name = "omop"

[resources.cdm_db]
database = "main"
cdm_schema = "cdm"
""",
    )
    snapshot = load_configuration(config_path=path)
    draft = draft_from_plan(plan_database_configuration(snapshot), snapshot)
    draft = replace(draft, password_action=PasswordAction.CLEAR)

    apply_database_configuration(snapshot, draft)

    reloaded = load_configuration(config_path=path)
    assert reloaded.stack.databases["main"].password is None


def test_shared_connection_clone_repoints_only_groundworkers(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"

[resources.other_app]
database = "main"
cdm_schema = "main"
""",
    )
    snapshot = load_configuration(config_path=path)
    plan = plan_database_configuration(snapshot)
    assert plan.shared_connection

    draft = draft_from_plan(plan, snapshot)
    draft = replace(
        draft,
        connection_strategy=ConnectionStrategy.CLONE,
        source_connection_name="main",
        connection_name="groundworkers_main",
    )

    apply_database_configuration(snapshot, draft)

    reloaded = load_configuration(config_path=path)
    assert reloaded.stack.resources["cdm_db"].database == "groundworkers_main"
    assert reloaded.stack.resources["other_app"].database == "main"


def test_profile_overridden_target_is_read_only(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
active_profile = "dev"

[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.dev]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"

[profiles.dev.resources.cdm_db]
database = "dev"
cdm_schema = "main"
""",
    )
    snapshot = load_configuration(config_path=path)
    plan = plan_database_configuration(snapshot)

    assert plan.editable is False
    with pytest.raises(PermissionError):
        apply_database_configuration(snapshot, draft_from_plan(plan, snapshot))


def test_candidate_targets_do_not_persist(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    draft = DatabaseConfigurationDraft(
        connection_name="candidate",
        dialect="sqlite",
        database_name=":memory:",
    )

    targets = candidate_targets(draft)

    assert targets[0].safe_url == "sqlite:///:memory:"
    assert not path.exists()


def test_stale_revision_rejects_apply(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
""",
    )
    snapshot = load_configuration(config_path=path)
    path.write_text(path.read_text(encoding="utf-8") + "\n[tools.other]\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after it was opened"):
        apply_database_configuration(
            snapshot,
            draft_from_plan(plan_database_configuration(snapshot), snapshot),
        )


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path
