from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from omop_alchemy.cdm.model.vocabulary import Domain
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class DomainNameResolver:
    """Resolve a loosely-typed domain to its canonical ``domain_id``.

    The cache is populated on first use and never invalidated: the CDM ``domain``
    table is reference data that changes only with a vocabulary reload, which
    restarts the server anyway.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._by_lowered: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._by_lowered is not None:
            return self._by_lowered

        mapping: dict[str, str] = {}
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(select(Domain.domain_id, Domain.domain_name)).all()
        except Exception:
            logger.warning("Could not read the domain table; domain names unresolved.", exc_info=True)
            self._by_lowered = {}
            return self._by_lowered

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
