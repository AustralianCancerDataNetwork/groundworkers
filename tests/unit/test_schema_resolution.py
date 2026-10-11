"""Stack 2.0 role routing and vocabulary reflection contracts."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from oa_configurator import ConnectionConfig, Role
from sqlalchemy.dialects import postgresql

import groundworkers.services.graph as graph_service
from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.application.setup.databases import (
    DatabaseTarget,
    verify_database_target,
)
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.services.vocab import VocabService
from tests.support.stack_config import build_cdm_stack


def test_create_cdm_engines_supplies_each_role_map() -> None:
    config = build_app_config_from_stack(build_cdm_stack())
    options = config.cdm_vocab_engine.get_execution_options()
    role_map = options["schema_translate_map"]

    assert "primary" in role_map
    assert "vocab" in role_map
    assert config.cdm_engine is not None
    assert config.cdm_session_factory is not None
    config.cdm_engine.dispose()
    if config.cdm_vocab_engine is not config.cdm_engine:
        config.cdm_vocab_engine.dispose()


def test_relationship_mapping_is_omop_graphs_translated_table() -> None:
    rm = graph_service._RELATIONSHIP_MAPPING
    assert isinstance(rm, sa.Table)
    assert rm.schema == Role.PRIMARY
    compiled = str(
        sa.select(rm.c.relationship_id).compile(
            dialect=postgresql.dialect(),
            schema_translate_map={Role.PRIMARY: "omop"},
            compile_kwargs={"render_schema_translate": True},
        )
    )
    assert "__[SCHEMA_primary].relationship_mapping" in compiled


def _split_sqlite_config(tmp_path: Path):
    stack = build_cdm_stack(sqlite_path=str(tmp_path / "primary.db"))
    stack.connections["vocab"] = ConnectionConfig(
        dialect="sqlite", database_name=str(tmp_path / "vocabulary.db")
    )
    stack.databases["cdm_db"].vocab_connection = "vocab"
    return build_app_config_from_stack(stack)


def _create_vocab_tables(engine: sa.Engine, *, sidecars: bool) -> None:
    concept_columns = "concept_id INTEGER, concept_name TEXT"
    synonym_columns = "concept_id INTEGER, concept_synonym_name TEXT"
    if sidecars:
        concept_columns += ", concept_name_tsvector TEXT"
        synonym_columns += ", concept_synonym_name_tsvector TEXT"
    with engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE TABLE concept ({concept_columns})")
        connection.exec_driver_sql(f"CREATE TABLE concept_synonym ({synonym_columns})")


def test_vocab_reflection_uses_the_vocab_role_schema(tmp_path: Path) -> None:
    config = _split_sqlite_config(tmp_path)
    _create_vocab_tables(config.cdm_vocab_engine, sidecars=True)
    service = VocabService(
        CDMAdapter(
            config.cdm_engine,
            vocab_engine=config.cdm_vocab_engine,
            session_factory=config.cdm_session_factory,
        )
    )
    assert service.fts_available is True
    config.cdm_engine.dispose()
    if config.cdm_vocab_engine is not config.cdm_engine:
        config.cdm_vocab_engine.dispose()


def test_vocab_reflection_reports_missing_sidecars(tmp_path: Path) -> None:
    config = _split_sqlite_config(tmp_path)
    _create_vocab_tables(config.cdm_vocab_engine, sidecars=False)
    service = VocabService(
        CDMAdapter(
            config.cdm_engine,
            vocab_engine=config.cdm_vocab_engine,
            session_factory=config.cdm_session_factory,
        )
    )
    assert service.fts_available is False
    config.cdm_engine.dispose()
    if config.cdm_vocab_engine is not config.cdm_engine:
        config.cdm_vocab_engine.dispose()


def test_normalized_index_check_uses_the_split_vocab_engine(tmp_path, monkeypatch) -> None:
    config = _split_sqlite_config(tmp_path)
    _create_vocab_tables(config.cdm_vocab_engine, sidecars=False)
    service = VocabService(
        CDMAdapter(
            config.cdm_engine,
            vocab_engine=config.cdm_vocab_engine,
            session_factory=config.cdm_session_factory,
        )
    )
    seen = []

    def record_connection(connection, *_args):
        seen.append(connection.engine.url.database)
        return False

    monkeypatch.setattr(
        "groundworkers.application.setup.databases.normalized_index_matches",
        record_connection,
    )
    assert service._has_normalized_index("concept", "concept_name") is False
    assert seen == [str(tmp_path / "vocabulary.db")]
    config.cdm_engine.dispose()
    config.cdm_vocab_engine.dispose()


def test_readiness_uses_configured_vocab_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "omop.db"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as connection:
        for name in (
            "concept", "concept_relationship", "concept_ancestor",
            "concept_synonym", "relationship",
        ):
            connection.exec_driver_sql(f"CREATE TABLE {name} (id INTEGER)")
    engine.dispose()
    target = DatabaseTarget(
        key="database.cdm", label="CDM / vocabulary", database_entry_name="cdm_db",
        connection_name="main", safe_url=f"sqlite:///{db_path}",
        cdm_schema=None, vocabulary_schema=None, connection_url=f"sqlite:///{db_path}",
        role="cdm",
    )
    result = verify_database_target(target)
    assert result.connected is True
    assert [item.code for item in result.diagnostics] == ["cdm_tables_present"]
