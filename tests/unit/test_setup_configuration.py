from __future__ import annotations

from pathlib import Path

import pytest
from oa_configurator import DatabaseConfig, ResourceConfig, StackConfig

from groundworkers.application.setup.configuration import (
    load_configuration,
    save_configuration,
)
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationState,
    OwnershipMode,
)


VALID_DATABASE_CONFIG = """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"
"""


def test_missing_configuration_is_typed(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"

    snapshot = load_configuration(config_path=path)

    assert snapshot.state is ConfigurationState.MISSING
    assert snapshot.path == path.resolve()
    assert snapshot.stack is None
    assert snapshot.issues[0].code == "config_missing"


def test_malformed_configuration_does_not_echo_secrets(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[databases.main]\npassword = "super-secret"\nbroken = [\n',
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
    assert {issue.code for issue in snapshot.issues} == {"cdm_resource_unresolved"}


def test_missing_profile_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")

    snapshot = load_configuration(config_path=path, profile="production")

    assert snapshot.state is ConfigurationState.INCOMPLETE
    assert snapshot.issues[0].code == "active_profile_missing"


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
        databases={
            "main": DatabaseConfig(dialect="sqlite", database_name="replacement.db")
        },
        resources={
            "cdm_db": ResourceConfig(
                database="main", cdm_schema="main", vocab_schema="main"
            )
        },
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
        databases={"main": DatabaseConfig(dialect="sqlite", database_name=":memory:")},
        resources={
            "cdm_db": ResourceConfig(
                database="main", cdm_schema="main", vocab_schema="main"
            )
        },
    )
    ownership = ConfigurationOwnership(mode=OwnershipMode.DERIVED_READ_ONLY)

    with pytest.raises(PermissionError, match="read-only"):
        save_configuration(
            stack,
            path=path,
            expected_revision=None,
            ownership=ownership,
        )

    assert path.exists() is False
