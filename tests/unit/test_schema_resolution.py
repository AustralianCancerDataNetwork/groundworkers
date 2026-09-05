"""How a non-default CDM schema reaches every statement Groundworkers issues.

oa-configurator expresses schema placement only through SQLAlchemy's
``schema_translate_map`` and sets no ``search_path``. The map is applied when a
statement is *compiled*, so these tests cover the three places that fall outside
compilation and therefore have to be handled by hand: engine construction, Core
constructs that look translatable but are not, and reflection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import groundworkers.services.graph as graph_service
from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.application.setup.databases import (
    DatabaseTarget,
    verify_database_target,
)
from groundworkers.base.sql import effective_schema
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.services.vocab import VocabService
from tests.support.stack_config import build_cdm_stack

# ---------------------------------------------------------------------------
# effective_schema
# ---------------------------------------------------------------------------

def test_effective_schema_reads_the_binds_translate_map() -> None:
    engine = sa.create_engine("sqlite://").execution_options(
        schema_translate_map={None: "cdm", "vocab": "cdm_vocab"}
    )

    assert effective_schema(engine) == "cdm"
    with engine.connect() as connection:
        assert effective_schema(connection) == "cdm"


def test_effective_schema_is_none_without_a_map() -> None:
    """The correct answer for an unconfigured deployment, not a failure.

    ``schema=None`` is what reflection already wants when the dialect's own
    ``default_schema_name`` applies.
    """
    assert effective_schema(sa.create_engine("sqlite://")) is None


def test_effective_schema_ignores_a_map_without_a_default_key() -> None:
    engine = sa.create_engine("sqlite://").execution_options(
        schema_translate_map={"vocab": "cdm_vocab"}
    )

    assert effective_schema(engine) is None


# ---------------------------------------------------------------------------
# The runtime engine
# ---------------------------------------------------------------------------

def test_the_cdm_engine_carries_the_resolved_translate_map() -> None:
    """Built from the database, not the connection.

    ``ResolvedConnection.create_engine`` produces an engine with no map at all,
    which silently resolves every CDM table through the ambient ``search_path``.
    """
    stack = build_cdm_stack(schema_name="cdm", vocab_schema="cdm_vocab")

    config = build_app_config_from_stack(stack)

    assert config.cdm_engine.get_execution_options()["schema_translate_map"] == {
        None: "cdm",
        "vocab": "cdm_vocab",
    }
    assert effective_schema(config.cdm_engine) == "cdm"


def test_the_cdm_engine_map_matches_the_resolved_database() -> None:
    stack = build_cdm_stack(schema_name="cdm", vocab_schema=None)

    config = build_app_config_from_stack(stack)

    assert (
        config.cdm_engine.get_execution_options()["schema_translate_map"]
        == config.cdm_database.schema_translate_map()
    )


# ---------------------------------------------------------------------------
# The relationship_mapping sidecar reference
# ---------------------------------------------------------------------------

def test_relationship_mapping_is_a_translated_table() -> None:
    """A lowercase ``table()`` would compile unqualified even with a map set.

    That is the failure this guards: one silently unqualified table in an
    otherwise correctly routed query.
    """
    rm = graph_service._RELATIONSHIP_MAPPING

    assert isinstance(rm, sa.Table)
    assert rm.schema is None

    compiled = str(
        sa.select(rm.c.relationship_id).compile(
            dialect=postgresql.dialect(), schema_translate_map={None: "cdm"}
        )
    )
    assert "__[SCHEMA__none].relationship_mapping" in compiled


def test_relationship_mapping_is_omop_graphs_own_definition() -> None:
    """One definition, not a hand-written copy that can drift from upstream."""
    from omop_graph.extensions.omop_alchemy import RelationshipMapping

    assert graph_service._RELATIONSHIP_MAPPING is RelationshipMapping.__table__


def test_services_do_not_use_the_untranslated_table_helper() -> None:
    """`sa.table()` looks schema-agnostic but is excluded from translation."""
    offenders = [
        f"{path.name}:{number}"
        for path in (Path(graph_service.__file__).parent).glob("**/*.py")
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if line.startswith("from sqlalchemy import")
        and "table" in [part.strip() for part in line.split("import", 1)[1].split(",")]
    ]

    assert offenders == []


# ---------------------------------------------------------------------------
# Reflection
# ---------------------------------------------------------------------------

def _attached_cdm_engine(tmp_path: Path, *, sidecars: bool) -> sa.Engine:
    """An engine whose CDM tables live in a non-default schema.

    SQLite's ``ATTACH`` stands in for a named schema: reflection without an
    explicit ``schema=`` raises ``NoSuchTableError`` for these tables, exactly as
    an unqualified reflection does on PostgreSQL when the tables are outside
    ``search_path``.
    """
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'main.db'}")
    attached = tmp_path / "cdm.db"

    @sa.event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):  # pragma: no cover - connection hook
        dbapi_connection.execute(f"ATTACH DATABASE '{attached}' AS cdm")

    concept_columns = "concept_id INTEGER, concept_name TEXT"
    synonym_columns = "concept_id INTEGER, concept_synonym_name TEXT"
    if sidecars:
        concept_columns += ", concept_name_tsvector TEXT"
        synonym_columns += ", concept_synonym_name_tsvector TEXT"
    with engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE TABLE cdm.concept ({concept_columns})")
        connection.exec_driver_sql(
            f"CREATE TABLE cdm.concept_synonym ({synonym_columns})"
        )

    return engine.execution_options(schema_translate_map={None: "cdm"})


def test_fulltext_sidecars_are_detected_in_the_configured_schema(
    tmp_path: Path,
) -> None:
    service = VocabService(CDMAdapter(_attached_cdm_engine(tmp_path, sidecars=True)))

    assert service.fts_available is True


def test_absent_fulltext_sidecars_are_reported_as_unavailable(tmp_path: Path) -> None:
    """Distinguishes "no sidecar" from "could not look" -- both must say False."""
    service = VocabService(CDMAdapter(_attached_cdm_engine(tmp_path, sidecars=False)))

    assert service.fts_available is False


def test_unreadable_vocabulary_logs_rather_than_absorbing_the_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'empty.db'}")
    service = VocabService(
        CDMAdapter(engine.execution_options(schema_translate_map={None: "absent"}))
    )

    with caplog.at_level("WARNING"):
        assert service.fts_available is False

    assert "absent" in caplog.text


# ---------------------------------------------------------------------------
# Setup diagnostics
# ---------------------------------------------------------------------------

def test_readiness_checks_the_schema_the_runtime_reads(tmp_path: Path) -> None:
    """Keyed on cdm_schema, not vocabulary_schema.

    No omop-alchemy model declares ``schema="vocab"``, so vocabulary tables live
    in the primary schema. Checking ``vocabulary_schema`` reports them missing on
    any config that sets the two differently -- the inverse of what the runtime
    engine finds.
    """
    db_path = tmp_path / "omop.db"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as connection:
        for name in (
            "concept",
            "concept_relationship",
            "concept_ancestor",
            "concept_synonym",
            "relationship",
        ):
            connection.exec_driver_sql(f"CREATE TABLE {name} (id INTEGER)")
    engine.dispose()

    target = DatabaseTarget(
        key="database.cdm",
        label="CDM / vocabulary",
        database_entry_name="cdm_db",
        connection_name="main",
        safe_url=f"sqlite:///{db_path}",
        # The tables are in "main"; "temp" is a real but empty SQLite schema.
        cdm_schema="main",
        vocabulary_schema="temp",
        connection_url=f"sqlite:///{db_path}",
        role="cdm",
    )

    result = verify_database_target(target)

    assert result.connected is True
    assert [d.code for d in result.diagnostics] == ["cdm_tables_present"]


# ---------------------------------------------------------------------------
# The removed parameter
# ---------------------------------------------------------------------------

def test_the_graph_adapter_rejects_a_vocabulary_schema_argument() -> None:
    """It never did anything; accepting it again would be a silent no-op."""
    engine = sa.create_engine("sqlite://")

    with pytest.raises(TypeError):
        OmopGraphAdapter(engine, vocab_schema="omop_vocab")  # type: ignore[call-arg]
