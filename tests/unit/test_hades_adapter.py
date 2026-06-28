from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import sqlalchemy as sa
import sqlalchemy.orm as so

from groundworkers.adapters.hades import DatabaseRecord, HadesResultsAdapter
from groundworkers.base.errors import GroundworkersError
from hades_results_alchemy.models import HadesResultsConfig


# ---------------------------------------------------------------------------
# Inline CSE CSV — avoids dependency on cloned external repo
# ---------------------------------------------------------------------------

_CSE_CSV = textwrap.dedent("""\
    table_name,column_name,data_type,primary_key,min_cell_count,description,empty_is_na
    cohort_definition,cohort_definition_id,bigint,Yes,no,cohort definition id,Yes
    cohort_definition,cohort_definition_name,varchar,no,no,cohort definition name,Yes
    cdm_source_info,database_id,bigint,Yes,no,database definition id,Yes
    cdm_source_info,cdm_source_abbreviation,varchar,no,no,cdm info,Yes
    cdm_source_info,cdm_holder,varchar,no,no,cdm info,Yes
    cdm_source_info,source_description,varchar,no,no,cdm info,Yes
    cdm_source_info,source_documentation_reference,varchar,no,no,cdm info,Yes
    cdm_source_info,cdm_etl_reference,varchar,no,no,cdm info,Yes
    cdm_source_info,source_release_date,date,no,no,cdm info,Yes
    cdm_source_info,cdm_release_date,date,no,no,cdm info,Yes
    cdm_source_info,cdm_version,varchar,no,no,cdm info,Yes
    cdm_source_info,vocabulary_version,varchar,no,no,cdm info,Yes
""")


class _ConcreteAdapter(HadesResultsAdapter):
    """Minimal concrete subclass for testing the base class."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cse_csv(tmp_path: Path) -> Path:
    p = tmp_path / "resultsDataModel.csv"
    p.write_text(_CSE_CSV)
    return p


@pytest.fixture
def cse_spec(cse_csv: Path):
    from hades_results_alchemy.spec import HadesSpec
    return HadesSpec.from_csv(cse_csv)


@pytest.fixture
def sqlite_engine():
    engine = sa.create_engine("sqlite:///:memory:")
    yield engine
    engine.dispose()


@pytest.fixture
def populated_db(cse_spec, sqlite_engine):
    """SQLite in-memory DB with cdm_source_info populated."""
    cse_spec.metadata.create_all(sqlite_engine)
    CdmSourceInfo = cse_spec.CdmSourceInfo
    with so.Session(sqlite_engine) as session:
        session.add_all([
            CdmSourceInfo(database_id=1, cdm_source_abbreviation="DB1", source_description="First"),
            CdmSourceInfo(database_id=2, cdm_source_abbreviation="DB2", source_description="Second"),
        ])
        session.commit()
    return sqlite_engine


@pytest.fixture
def adapter(populated_db, cse_spec):
    config = HadesResultsConfig(db_url=str(populated_db.url))
    a = _ConcreteAdapter(config, spec=cse_spec)
    a._engine = populated_db
    return a


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_is_available(adapter):
    assert adapter.is_available() is True


def test_is_available_bad_url():
    config = HadesResultsConfig(db_url="sqlite:////nonexistent/path/db.sqlite")
    adapter = _ConcreteAdapter(config)
    assert adapter.is_available() is False


def test_list_databases(adapter):
    dbs = adapter.list_databases()
    assert len(dbs) == 2
    assert dbs[0].database_id == 1
    assert dbs[0].cdm_source_abbreviation == "DB1"
    assert dbs[1].database_id == 2


def test_list_databases_returns_database_records(adapter):
    dbs = adapter.list_databases()
    assert all(isinstance(d, DatabaseRecord) for d in dbs)


def test_table_name_no_prefix():
    config = HadesResultsConfig(db_url="sqlite:///:memory:")
    adapter = _ConcreteAdapter(config)
    assert adapter._table_name("cohort_definition") == "cohort_definition"


def test_table_name_with_prefix():
    config = HadesResultsConfig(db_url="sqlite:///:memory:", table_prefix="cse_")
    adapter = _ConcreteAdapter(config)
    assert adapter._table_name("cohort_definition") == "cse_cohort_definition"


def test_table_fully_qualified_no_schema():
    config = HadesResultsConfig(db_url="sqlite:///:memory:", table_prefix="cse_")
    adapter = _ConcreteAdapter(config)
    assert adapter._table("cohort_definition") == "cse_cohort_definition"


def test_table_fully_qualified_with_schema():
    config = HadesResultsConfig(db_url="sqlite:///:memory:", table_prefix="cse_", schema_name="results")
    adapter = _ConcreteAdapter(config)
    assert adapter._table("cohort_definition") == "results.cse_cohort_definition"


def test_validate_schema_phase1_stub(adapter):
    assert adapter.validate_schema() == []


def test_get_schema_version_phase1_stub(adapter):
    assert adapter.get_schema_version() is None


def test_close(adapter):
    adapter.close()
    assert adapter._engine is None


def test_validate_on_connect_raises_groundworkers_error(cse_spec, sqlite_engine):
    """validate_on_connect=True raises GroundworkersError when issues found."""
    cse_spec.metadata.create_all(sqlite_engine)

    class _StrictAdapter(_ConcreteAdapter):
        def validate_schema(self, spec_path=None):
            return ["missing table: cse_foo"]

    config = HadesResultsConfig(
        db_url=str(sqlite_engine.url), validate_on_connect=True
    )
    with pytest.raises(GroundworkersError) as exc_info:
        a = _StrictAdapter(config)
        a._engine = sqlite_engine
    assert exc_info.value.code == "BACKEND_UNAVAIL"
