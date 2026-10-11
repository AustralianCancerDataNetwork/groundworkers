from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from omop_alchemy.cdm.model.vocabulary import Domain
from sqlalchemy import select
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _is_missing_domain_table(exc: Exception) -> bool:
    """Recognize database errors caused specifically by the absent domain table."""
    pending: list[object] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if "no such table" in str(current).lower():
            return True
        if getattr(current, "sqlstate", None) == "42P01" or getattr(
            current, "pgcode", None
        ) == "42P01":
            return True
        for attr in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attr, None)
            if nested is not None:
                pending.append(nested)
    return False


class DomainNameResolver:
    """Resolve a loosely-typed domain to its canonical ``domain_id``.

    The cache is populated on first use and never invalidated: the CDM ``domain``
    table is reference data that changes only with a vocabulary reload, which
    restarts the server anyway.
    """

    def __init__(self, engine: Engine | Callable[[], Any]) -> None:
        if isinstance(engine, Engine):
            self._engine: Engine | None = engine
            self._session_factory: Callable[[], Any] | None = None
        else:
            self._engine = None
            self._session_factory = engine
        self._by_lowered: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._by_lowered is not None:
            return self._by_lowered

        mapping: dict[str, str] = {}
        try:
            if self._session_factory is not None:
                with self._session_factory() as session:
                    rows = session.execute(
                        select(Domain.domain_id, Domain.domain_name)
                    ).all()
            else:
                assert self._engine is not None
                with self._engine.connect() as conn:
                    rows = conn.execute(
                        select(Domain.domain_id, Domain.domain_name)
                    ).all()
        except Exception as exc:
            logger.warning("Could not read the domain table; domain names unresolved.", exc_info=True)
            if self._engine is not None and _is_missing_domain_table(exc):
                raise RuntimeError(
                    "Engine mode cannot route vocabulary queries. If this deployment "
                    "keeps the vocabulary on its own database, pass "
                    "cdm_session_factory (PluginContext.cdm_session_factory) "
                    "to DomainNameResolver."
                ) from exc
            if self._engine is not None:
                raise
            # Leave the cache empty so transient outages are retried next time.
            return mapping

        for domain_id, domain_name in rows:
            if not domain_id:
                continue
            mapping[domain_id.strip().lower()] = domain_id
            if domain_name:
                mapping.setdefault(domain_name.strip().lower(), domain_id)

        self._by_lowered = mapping
        return mapping

    def canonical(self, value: str | None) -> str | None:
        """Return the canonical ``domain_id``, or ``None`` if unrecognised.

        ``None`` input returns ``None`` — no constraint requested, which is
        distinct from an unrecognised one. Callers must tell those apart before
        treating the result as an error.
        """
        if value is None:
            return None
        lookup = self._load()
        if not lookup:
            return value
        return lookup.get(value.strip().lower())

    def known_domains(self) -> tuple[str, ...]:
        """Every valid ``domain_id``, sorted — usable as a select list."""
        return tuple(sorted(set(self._load().values())))

    def describe_unknown(self, value: str) -> str:
        """An error message naming the value and a sample of valid alternatives."""
        known = self.known_domains()
        if not known:
            return f"Unknown domain {value!r}."
        preview = ", ".join(known[:8])
        suffix = f", ... ({len(known)} total)" if len(known) > 8 else ""
        return f"Unknown domain {value!r}. Valid domains include: {preview}{suffix}."


__all__ = ["DomainNameResolver"]
