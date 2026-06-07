from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


class CDMAdapter:
    """Adapter for a CDM (Common Data Model) database connection.

    Holds the SQLAlchemy engine and session factory for an OMOP CDM database.
    Shared by services that need to query the CDM directly (VocabService,
    OmopGraphAdapter).

    Pass engine to adapters that wrap their own session management.
    Use session() for services that need a scoped session context manager.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(engine)

    @property
    def engine(self) -> Engine:
        """The underlying SQLAlchemy engine."""
        return self._engine

    def session(self):
        """Return a session context manager."""
        return self._session_factory()

    def is_available(self) -> bool:
        """Return True if the CDM database is reachable (SELECT 1 probe)."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Dispose the engine and release the connection pool."""
        self._engine.dispose()
