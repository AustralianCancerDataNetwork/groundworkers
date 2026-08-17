from __future__ import annotations

import socket
from pathlib import Path

from sqlalchemy import create_engine, text

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.databases import (
    _index_defines_lower_expression,
    classify_connection_error,
    resolve_database_targets,
    verify_database_target,
)
from groundworkers.application.setup.models import ConnectionFailureKind, DatabaseTarget

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


def test_database_targets_are_resolved_with_safe_urls(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")

    targets = resolve_database_targets(load_configuration(config_path=path))

    assert len(targets) == 3
    assert targets[0].key == "database.cdm"
    assert targets[1].key == "database.graph"
    assert targets[2].key == "database.groundworkers"
    assert targets[0].safe_url == "sqlite:///:memory:"
    assert "connection_url" not in repr(targets[0])


def test_distinct_vocabulary_connection_is_a_separate_target(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[connections.cdm]
dialect = "sqlite"
database_name = ":memory:"

[connections.vocabulary]
dialect = "sqlite"
database_name = "vocabulary.db"

[databases.cdm_db]
kind = "cdm"
connection = "cdm"
schema_name = "main"
vocab_connection = "vocabulary"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )

    targets = resolve_database_targets(load_configuration(config_path=path))

    vocabulary = next(
        target for target in targets if target.key == "database.vocabulary"
    )
    assert vocabulary.connection_name == "vocabulary"
    assert vocabulary.safe_url.endswith("vocabulary.db")


def test_database_verification_records_latency(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(VALID_DATABASE_CONFIG, encoding="utf-8")
    target = resolve_database_targets(load_configuration(config_path=path))[0]
    ticks = iter((10.0, 10.038))

    result = verify_database_target(target, clock=lambda: next(ticks))

    assert result.connected is True
    assert result.latency_ms == 38.0
    assert result.has_warnings is True


def test_cdm_readiness_reports_present_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "omop.db"
    _create_tables(
        db_path,
        (
            "concept",
            "concept_relationship",
            "concept_ancestor",
            "concept_synonym",
            "relationship",
        ),
    )
    target = _target(db_path, role="cdm")

    result = verify_database_target(target)

    assert result.connected is True
    assert result.has_warnings is False
    assert result.diagnostics[0].code == "cdm_tables_present"


def test_graph_readiness_reports_missing_sidecars_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "omop.db"
    _create_tables(
        db_path,
        (
            "concept",
            "concept_relationship",
            "concept_ancestor",
            "concept_synonym",
            "relationship",
            "relationship_class",
            "relationship_mapping",
        ),
    )
    target = _target(db_path, role="graph")

    result = verify_database_target(target)

    assert result.connected is True
    assert result.has_warnings is True
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "graph_tables_empty",
        "fulltext_sidecar_missing",
        "fulltext_indexes_missing",
        "functional_indexes_missing",
    }


def test_functional_index_detection_accepts_hand_named_postgres_expressions() -> None:
    assert _index_defines_lower_expression(
        "CREATE INDEX idx_concept_lower_name "
        "ON public.concept USING btree (lower((concept_name)::text))",
        "concept_name",
    )
    assert _index_defines_lower_expression(
        "CREATE INDEX idx_concept_lower_name_trgm "
        "ON public.concept USING gin (lower(concept_name::text) gin_trgm_ops)",
        "concept_name",
        required_fragment="gin_trgm_ops",
    )
    assert not _index_defines_lower_expression(
        "CREATE INDEX idx_concept_lower_name "
        "ON public.concept USING btree (lower((concept_name)::text))",
        "concept_name",
        required_fragment="gin_trgm_ops",
    )


def test_groundworkers_tuning_reports_missing_trigram_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "omop.db"
    _create_tables(db_path, ("concept", "concept_synonym"))
    target = _target(db_path, role="groundworkers")

    result = verify_database_target(target)

    assert result.connected is True
    assert result.has_warnings is True
    assert result.diagnostics[0].code == "trigram_indexes_missing"


def test_embedding_readiness_reports_valid_registered_model(tmp_path: Path) -> None:
    db_path = tmp_path / "emb.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE model_registry (
                    model_name TEXT PRIMARY KEY,
                    storage_identifier TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    index_type TEXT NOT NULL,
                    metric_type TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO model_registry
                    (model_name, storage_identifier, dimensions, index_type, metric_type)
                VALUES ('test-model', 'emb_test_model', 3, 'FLAT', NULL)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE emb_test_model (
                    concept_id INTEGER PRIMARY KEY,
                    domain_id TEXT NOT NULL,
                    vocabulary_id TEXT NOT NULL,
                    is_standard BOOLEAN NOT NULL,
                    is_valid BOOLEAN NOT NULL,
                    embedding BLOB NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO emb_test_model
                    (concept_id, domain_id, vocabulary_id, is_standard, is_valid, embedding)
                VALUES (1, 'Condition', 'SNOMED', 1, 1, x'000102')
                """
            )
        )
    engine.dispose()

    result = verify_database_target(_target(db_path, role="embedding"))

    assert result.connected is True
    assert result.has_warnings is False
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "embedding_registry_present",
        "embedding_table_valid",
    }


def test_groundworkers_tuning_warns_when_grounding_model_is_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "emb.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE "concept" (id INTEGER PRIMARY KEY)'))
        connection.execute(
            text('CREATE TABLE "concept_synonym" (id INTEGER PRIMARY KEY)')
        )
        connection.execute(
            text(
                """
                CREATE TABLE model_registry (
                    model_name TEXT PRIMARY KEY,
                    storage_identifier TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    index_type TEXT NOT NULL,
                    metric_type TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO model_registry
                    (model_name, storage_identifier, dimensions, index_type, metric_type)
                VALUES ('other-model', 'emb_other_model', 3, 'FLAT', NULL)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE emb_other_model (
                    concept_id INTEGER PRIMARY KEY,
                    domain_id TEXT NOT NULL,
                    vocabulary_id TEXT NOT NULL,
                    is_standard BOOLEAN NOT NULL,
                    is_valid BOOLEAN NOT NULL,
                    embedding BLOB NOT NULL
                )
                """
            )
        )
    engine.dispose()

    result = verify_database_target(
        _target(
            db_path,
            role="groundworkers",
            expected_model_name="missing-model",
            embedding_connection_url=f"sqlite:///{db_path}",
        )
    )

    assert result.connected is True
    assert result.has_warnings is True
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {
        "trigram_indexes_missing",
        "grounding_embedding_model_missing",
    }


def test_connection_errors_are_classified_without_original_message() -> None:
    cases = (
        (socket.gaierror("private-host.example"), ConnectionFailureKind.DNS),
        (ConnectionRefusedError("secret-host"), ConnectionFailureKind.REFUSED),
        (TimeoutError("secret-token"), ConnectionFailureKind.TIMEOUT),
        (ModuleNotFoundError("private-driver"), ConnectionFailureKind.DRIVER_MISSING),
        (
            RuntimeError('FATAL: role "secret-user" does not exist'),
            ConnectionFailureKind.AUTHENTICATION,
        ),
    )

    for error, expected in cases:
        failure = classify_connection_error(error)
        assert failure.kind is expected
        assert "secret" not in failure.detail
        assert "private" not in failure.detail


def _target(
    db_path: Path,
    *,
    role: str,
    expected_model_name: str | None = None,
    embedding_connection_url: str | None = None,
) -> DatabaseTarget:
    return DatabaseTarget(
        key=f"database.{role}",
        label=role,
        database_entry_name="cdm_db",
        connection_name="main",
        safe_url=f"sqlite:///{db_path}",
        cdm_schema="main",
        vocabulary_schema="main",
        connection_url=f"sqlite:///{db_path}",
        role=role,
        expected_embedding_model_name=expected_model_name,
        embedding_safe_url=embedding_connection_url,
        embedding_connection_url=embedding_connection_url,
    )


def _create_tables(db_path: Path, names: tuple[str, ...]) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        for name in names:
            connection.execute(text(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)'))
    engine.dispose()


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path
