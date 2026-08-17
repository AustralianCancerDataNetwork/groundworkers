from __future__ import annotations

from pathlib import Path

import pytest
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, StackConfig

from groundworkers.application.setup.configuration import (
    load_configuration,
    missing_revision,
    save_configuration,
)
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationState,
    OwnershipMode,
)

VALID_DATABASE_CONFIG = """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
"""


def test_missing_configuration_is_typed(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"

    snapshot = load_configuration(config_path=path)

    assert snapshot.state is ConfigurationState.MISSING
    assert snapshot.path == path.resolve()
    assert snapshot.stack is None
    assert snapshot.revision == missing_revision(path)
    assert snapshot.issues[0].code == "config_missing"


def test_malformed_configuration_does_not_echo_secrets(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[connections.main]\npassword = "super-secret"\nbroken = [\n',
        encoding="utf-8",
    )

    snapshot = load_configuration(config_path=path)

    assert snapshot.state is ConfigurationState.MALFORMED
    assert "super-secret" not in repr(snapshot)
    assert "super-secret" not in " ".join(issue.message for issue in snapshot.issues)


def test_empty_stack_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")

    snapshot = load_configuration(config_path=path)

    assert snapshot.state is ConfigurationState.INCOMPLETE
    assert {issue.code for issue in snapshot.issues} == {
        "groundworkers_config_incomplete"
    }


def test_authoritative_configuration_loads_without_building_runtime(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")

    snapshot = load_configuration(config_path=path)

    assert snapshot.state is ConfigurationState.UNVERIFIED
    assert snapshot.usable is True
    assert snapshot.revision is not None
    assert snapshot.ownership.editable is True


def test_derived_ownership_is_supplied_by_setup_service(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")
    ownership = ConfigurationOwnership(
        mode=OwnershipMode.DERIVED_READ_ONLY,
        source_label="Institutional configuration manager",
        guidance="Edit this connection in the institutional settings service.",
    )

    snapshot = load_configuration(config_path=path, ownership=ownership)

    assert snapshot.ownership.editable is False
    assert snapshot.ownership.source_label == "Institutional configuration manager"


def test_save_rejects_stale_revision_and_preserves_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")
    snapshot = load_configuration(config_path=path)
    path.write_text(f"{VALID_DATABASE_CONFIG}\n# concurrent edit\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after it was opened"):
        save_configuration(
            snapshot.stack,
            path=path,
            expected_revision=snapshot.revision,
            ownership=snapshot.ownership,
        )

    assert "concurrent edit" in path.read_text(encoding="utf-8")


def test_save_backs_up_atomically_replaces_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")
    snapshot = load_configuration(config_path=path)
    replacement = StackConfig(
        connections={
            "main": ConnectionConfig(
                dialect="sqlite",
                database_name="replacement.db",
            )
        },
        databases={
            "cdm_db": CDMDatabaseConfig(
                connection="main",
                schema_name="main",
                vocab_schema="main",
            )
        },
        tools={"groundworkers": {"cdm_db": "cdm_db"}},
    )

    result = save_configuration(
        replacement,
        path=path,
        expected_revision=snapshot.revision,
        ownership=snapshot.ownership,
    )

    assert result.snapshot.usable is True
    assert result.backup_path is not None
    assert result.backup_path.read_text(encoding="utf-8") == VALID_DATABASE_CONFIG
    assert "replacement.db" in path.read_text(encoding="utf-8")
    assert result.restart_required is True


def test_save_rejects_derived_configuration(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    stack = StackConfig(
        connections={
            "main": ConnectionConfig(dialect="sqlite", database_name=":memory:")
        },
        databases={
            "cdm_db": CDMDatabaseConfig(connection="main", schema_name="main")
        },
        tools={"groundworkers": {"cdm_db": "cdm_db"}},
    )
    ownership = ConfigurationOwnership(mode=OwnershipMode.DERIVED_READ_ONLY)

    with pytest.raises(PermissionError, match="read-only"):
        save_configuration(
            stack,
            path=path,
            expected_revision=missing_revision(path),
            ownership=ownership,
        )

    assert path.exists() is False
