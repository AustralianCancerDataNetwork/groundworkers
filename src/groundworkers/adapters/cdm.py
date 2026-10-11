from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import literal, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class CDMAdapter:
    """Adapter for a CDM (Common Data Model) database connection.

    Holds the SQLAlchemy engine and session factory for an OMOP CDM database.
    Shared by services that need to query the CDM directly (VocabService,
    OmopGraphAdapter).

    Pass engine to adapters that wrap their own session management.
    Use session() for services that need a scoped session context manager.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        vocab_engine: Engine | None = None,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._engine = engine
        self._vocab_engine = vocab_engine or engine
        self._session_factory = session_factory or sessionmaker(bind=engine)

    @property
    def engine(self) -> Engine:
        """The underlying SQLAlchemy engine."""
        return self._engine

    @property
    def vocab_engine(self) -> Engine:
        """The engine routed to the OMOP vocabulary role."""
        return self._vocab_engine

    @property
    def session_factory(self) -> Callable[[], Session]:
        """The session factory routed across the primary and vocabulary roles."""
        return self._session_factory

    def session(self):
        """Return a session context manager."""
        return self._session_factory()

    def is_available(self) -> bool:
        """Return True if the CDM database is reachable (SELECT 1 probe)."""
        try:
            with self._engine.connect() as conn:
                conn.execute(select(literal(1)))
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Dispose the engine and release the connection pool."""
        self._engine.dispose()
        if self._vocab_engine is not self._engine:
            self._vocab_engine.dispose()
