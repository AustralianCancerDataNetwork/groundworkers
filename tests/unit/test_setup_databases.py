from __future__ import annotations

import socket
from pathlib import Path

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.databases import (
    classify_connection_error,
    resolve_database_targets,
    verify_database_target,
)
from groundworkers.application.setup.models import ConnectionFailureKind


VALID_DATABASE_CONFIG = """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"
"""


def test_database_targets_are_resolved_with_safe_urls(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")

    targets = resolve_database_targets(load_configuration(config_path=path))

    assert len(targets) == 1
    assert targets[0].key == "database.cdm"
    assert targets[0].safe_url == "sqlite:///:memory:"
    assert "connection_url" not in repr(targets[0])


def test_database_verification_records_latency(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")
    target = resolve_database_targets(load_configuration(config_path=path))[0]
    ticks = iter((10.0, 10.038))

    result = verify_database_target(target, clock=lambda: next(ticks))

    assert result.connected is True
    assert result.latency_ms == 38.0


def test_connection_errors_are_classified_without_original_message() -> None:
    cases = (
        (socket.gaierror("private-host.example"), ConnectionFailureKind.DNS),
        (ConnectionRefusedError("secret-host"), ConnectionFailureKind.REFUSED),
        (TimeoutError("secret-token"), ConnectionFailureKind.TIMEOUT),
        (ModuleNotFoundError("private-driver"), ConnectionFailureKind.DRIVER_MISSING),
    )

    for error, expected in cases:
        failure = classify_connection_error(error)
        assert failure.kind is expected
        assert "secret" not in failure.detail
        assert "private" not in failure.detail
