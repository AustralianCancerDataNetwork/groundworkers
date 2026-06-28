from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlalchemy as sa

from groundworkers.base.errors import GroundworkersError

try:
    from hades_results_alchemy.models import HadesResultsConfig
    from hades_results_alchemy.spec import HadesSpec
except ImportError as exc:
    raise ImportError(
        "hades-results-alchemy is required for groundworkers.adapters.hades. "
        "Install: pip install 'groundworkers[hades]'"
    ) from exc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabaseRecord:
    """CDM source entry from cdm_source_info."""
    database_id: int
    cdm_source_abbreviation: str
    source_description: str | None
    cdm_release_date: date | None


class HadesResultsAdapter(ABC):
    """Base adapter for read-only access to a HADES results database.

    Python interface to HadesResults is currently read-only. 
    This adapter never creates tables, never runs
    migrations, and never writes to the results database. 
    The R package owns the schema via ResultModelManager.

    Subclasses live in service repos and define domain query methods. All
    query methods should accept ``database_ids`` as a keyword-only parameter
    and return tuples of typed frozen dataclasses.
    """

    def __init__(self, config: HadesResultsConfig, spec: HadesSpec | None = None) -> None:
        self._config = config
        self._spec = spec
        self._engine: sa.Engine | None = None
        # Built once at init; cdm_source_info has a stable column set across all HADES packages.
        self._cdm_source_info: sa.Table = sa.Table(
            self._table_name("cdm_source_info"), sa.MetaData(),
            sa.Column("database_id", sa.BigInteger, primary_key=True, nullable=False),
            sa.Column("cdm_source_abbreviation", sa.String),
            sa.Column("cdm_holder", sa.String),
            sa.Column("source_description", sa.String),
            sa.Column("source_documentation_reference", sa.String),
            sa.Column("cdm_etl_reference", sa.String),
            sa.Column("source_release_date", sa.Date),
            sa.Column("cdm_release_date", sa.Date),
            sa.Column("cdm_version", sa.String),
            sa.Column("vocabulary_version", sa.String),
            schema=config.schema_name,
        )

        if config.validate_on_connect:
            self._ensure_engine()
            issues = self.validate_schema(config.spec_path)
            if issues:
                raise GroundworkersError(
                    "BACKEND_UNAVAIL",
                    "Schema validation failed on connect:\n" + "\n".join(f"  - {i}" for i in issues),
                )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def spec(self) -> HadesSpec | None:
        """The HadesSpec this adapter was initialised with, if any."""
        return self._spec

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _ensure_engine(self) -> sa.Engine:
        if self._engine is None:
            self._engine = sa.create_engine(
                self._config.db_url,
                poolclass=sa.pool.NullPool,
            )
        return self._engine

    def is_available(self) -> bool:
        try:
            with self._ensure_engine().connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    # ------------------------------------------------------------------
    # Table name resolution
    # ------------------------------------------------------------------

    def _table_name(self, name: str) -> str:
        """Return the bare prefixed table name, without schema qualifier."""
        return f"{self._config.table_prefix}{name}"

    def _table(self, name: str) -> str:
        """Return the fully schema-qualified prefixed table name.

        Use for the two permitted ``sqlalchemy.text()`` queries only
        (``SELECT 1`` health probe and migration version read).
        """
        qualified = self._table_name(name)
        if self._config.schema_name:
            return f"{self._config.schema_name}.{qualified}"
        return qualified

    # ------------------------------------------------------------------
    # Standard queries
    # ------------------------------------------------------------------

    def list_databases(self) -> tuple[DatabaseRecord, ...]:
        """Return all CDM sources present in the results database."""
        tbl = self._cdm_source_info
        try:
            with self._ensure_engine().connect() as conn:
                rows = conn.execute(sa.select(tbl).order_by(tbl.c.database_id)).fetchall()
        except Exception as exc:
            raise GroundworkersError("QUERY_ERROR", str(exc)) from exc
        return tuple(
            DatabaseRecord(
                database_id=int(row.database_id),
                cdm_source_abbreviation=str(row.cdm_source_abbreviation or ""),
                source_description=row.source_description,
                cdm_release_date=_coerce_date(row.cdm_release_date),
            )
            for row in rows
        )

    def validate_schema(self, spec_path: str | None = None) -> list[str]:
        """Check tables and columns against a resultsDataModel.csv spec.

        Returns a list of issue strings; empty list means valid.
        Phase 1: stubbed — returns [] always.
        """
        return []

    def get_schema_version(self) -> str | None:
        """Return the highest migration_order from the ResultModelManager
        migration table, or None if the table is absent.

        Phase 1: stubbed — returns None always.
        """
        return None


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None
