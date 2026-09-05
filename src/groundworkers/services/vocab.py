from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
    Concept_Synonym,
)
from omop_alchemy.cdm.query import ConceptFilter
from sqlalchemy import column as sa_col
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.base.concept_payload import serialise_concept_view
from groundworkers.base.domain_names import DomainNameResolver
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.sql import effective_schema

logger = logging.getLogger(__name__)

# Keep this service focused on vocabulary queries. Reuse an omop-graph primitive
# when it already provides the required operation; higher-level policy belongs in
# a domain service.

# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class ConceptMatch:
    """A single candidate returned by search_exact, search_normalized, or search_fulltext."""
    #: Shared concept payload from ``base.concept_payload``, so search results
    #: report the same flags as every other concept-returning tool. Previously a
    #: parallel field list carrying ``standard_concept`` and raw
    #: ``invalid_reason`` but no ``classification_concept``, which meant search
    #: could not distinguish a classification concept from an unflagged one.
    concept: dict[str, Any]
    match_source: str           # "name" | "synonym"
    matched_synonym: str | None = None
    ts_rank: float | None = None

    @property
    def concept_id(self) -> int:
        """Convenience accessor; the identifier is carried in ``concept``."""
        return int(self.concept["concept_id"])

    @property
    def concept_name(self) -> str:
        return self.concept["concept_name"]


@dataclass
class MappedConcept:
    """A concept reached from a source concept, plus the edge that reached it.

    ``concept`` is a payload from ``base.concept_payload``, not a parallel field
    list. This used to be five hand-listed identity fields, which meant mapping
    results silently lacked ``concept_code``, ``is_active`` and
    ``classification_concept`` — the flags every other concept payload reports.
    Composing the shared payload means an upstream field reaches these results
    without editing anything here.
    """

    concept: dict[str, Any]
    #: ``"Maps to"``, ``"Maps to value"``, or the sentinel ``"self"`` when the
    #: source concept was already standard and maps to itself.
    relationship_id: str


@dataclass
class ConceptMappingResult:
    """Relationship-driven mapping result for a single source concept_id.

    One shape for every navigation. ``navigate_to_standard`` and
    ``navigate_to_value`` previously returned two dataclasses that were
    field-for-field identical apart from whether the list was called
    ``standard_concepts`` or ``related_concepts``; the distinction lives at the
    wire boundary instead, where it carries meaning for the caller.
    """

    source_concept_id: int
    source_concept_name: str
    #: Strict and disjoint, as everywhere else: 'S' and 'C' respectively. A
    #: single boolean could not express standard / classification / neither.
    source_standard_concept: bool
    source_classification_concept: bool
    targets: list[MappedConcept] = field(default_factory=list)


#: Retained so existing type annotations and imports keep working; both
#: navigations now return the same shape.
StandardMapping = ConceptMappingResult
RelatedConceptMapping = ConceptMappingResult


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class VocabService:
    """Direct Python API for OMOP vocabulary search and concept navigation.

    Exposes raw quality signals (ts_rank, standard_concept flag) so callers
    can apply their own quality thresholds and decide whether to navigate
    non-standard results to their standard equivalents.

    Raises GroundworkersError for database or query errors.
    Raises ValueError for invalid arguments.
    """

    IDENTITY_RELATIONSHIP_IDS: frozenset[str] = frozenset({"Maps to"})
    VALUE_RELATIONSHIP_IDS: frozenset[str] = frozenset({"Maps to value"})
    UNIT_RELATIONSHIP_IDS: frozenset[str] = frozenset({"Maps to unit"})

    def __init__(self, cdm: CDMAdapter) -> None:
        self._cdm = cdm
        self._domain_names = DomainNameResolver(cdm.engine)
        self._fts_name_sidecar: bool | None = None
        self._fts_synonym_sidecar: bool | None = None

    # ------------------------------------------------------------------
    # FTS sidecar detection
    # ------------------------------------------------------------------

    def _detect_fts_sidecars(self) -> None:
        """Detect and cache tsvector sidecar column presence (runs once).

        Reflection ignores ``schema_translate_map``, so the schema has to be
        supplied explicitly or this looks in the connection's default schema
        while every query in this service reads the configured one. Getting that
        wrong reports full-text as unavailable on a deployment where it is
        installed and populated -- a silent downgrade to the slower tiers, which
        is why the failure is logged rather than absorbed.
        """
        if self._fts_name_sidecar is not None:
            return
        schema = effective_schema(self._cdm.engine)
        try:
            inspector = sa_inspect(self._cdm.engine)
            concept_cols = {
                c["name"] for c in inspector.get_columns("concept", schema=schema)
            }
            synonym_cols = {
                c["name"]
                for c in inspector.get_columns("concept_synonym", schema=schema)
            }
            self._fts_name_sidecar = "concept_name_tsvector" in concept_cols
            self._fts_synonym_sidecar = "concept_synonym_name_tsvector" in synonym_cols
        except Exception:
            # Broad except: full-text is optional, so an unreadable vocabulary
            # degrades to the other tiers rather than failing the request. Logged
            # because "no sidecar" and "could not look" are different faults and
            # only one of them is a configuration error.
            logger.warning(
                "Could not inspect vocabulary tables in schema %r for full-text "
                "sidecar columns; treating full-text as unavailable.",
                schema,
                exc_info=True,
            )
            self._fts_name_sidecar = False
            self._fts_synonym_sidecar = False

    @property
    def fts_available(self) -> bool:
        """True when the concept_name_tsvector sidecar column is present."""
        self._detect_fts_sidecars()
        return bool(self._fts_name_sidecar)

    def probe_fulltext(self) -> tuple[bool, str | None]:
        """Smoke-test PostgreSQL full-text search with a representative term.

        A sidecar column and GIN index can exist while all sidecar values are
        NULL. Requiring one real match makes that state visible to health
        reporting instead of treating schema presence as operational readiness.
        """

        try:
            with self._cdm.session() as session:
                probe_label = session.execute(
                    select(Concept.concept_name)
                    .where(Concept.concept_name.is_not(None))
                    .where(Concept.concept_name != "")
                    .order_by(Concept.concept_id)
                    .limit(1)
                ).scalar_one_or_none()
            if not probe_label:
                return False, "No non-empty concept label is available for probing."
            results, available = self.search_fulltext(str(probe_label), limit=1)
        except GroundworkersError as exc:
            return False, exc.message
        except Exception as exc:
            return False, f"Full-text query probe failed with {type(exc).__name__}."
        if not available:
            return False, "Full-text sidecar columns are unavailable."
        if not results:
            return False, "Full-text smoke query returned no results."
        return True, None

    # ------------------------------------------------------------------
    # search_exact
    # ------------------------------------------------------------------

    def search_exact(
        self,
        query: str,
        *,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        active_only: bool = False,
        include_synonyms: bool = True,
        parent_ids: list[int] | None = None,
        limit: int = 20,
    ) -> list[ConceptMatch]:
        """Case-insensitive exact match against concept_name and optionally concept_synonym_name.

        standard_only defaults to False so the caller can inspect non-standard candidates
        and decide whether to navigate to their standard equivalents.

        Returns name matches before synonym matches; deduplicates by concept_id so a concept
        that matches both name and synonym only appears once.
        """
        q = query.strip()
        if not q:
            raise ValueError("query must be a non-empty string")

        results: list[ConceptMatch] = []
        seen_ids: set[int] = set()

        try:
            with self._cdm.session() as session:
                name_stmt = self._apply_concept_filters(
                    select(
                        Concept.concept_id,
                        Concept.concept_name,
                        Concept.concept_code,
                        Concept.vocabulary_id,
                        Concept.domain_id,
                        Concept.concept_class_id,
                        Concept.is_standard_expr().label("standard_concept"),
                        Concept.is_classification_expr().label("classification_concept"),
                        Concept.is_valid_expr().label("is_active"),
                    ).where(func.lower(Concept.concept_name) == q.lower()),
                    domain=domain,
                    vocabulary_id=vocabulary_id,
                    standard_only=standard_only,
                    active_only=active_only,
                    parent_ids=parent_ids,
                ).limit(limit)

                for row in session.execute(name_stmt).all():
                    seen_ids.add(int(row.concept_id))
                    results.append(_row_to_match(row, "name", None, None))

                if include_synonyms:
                    remaining = limit - len(results)
                    if remaining > 0:
                        syn_stmt = self._apply_concept_filters(
                            select(
                                Concept.concept_id,
                                Concept.concept_name,
                                Concept.concept_code,
                                Concept.vocabulary_id,
                                Concept.domain_id,
                                Concept.concept_class_id,
                                Concept.is_standard_expr().label("standard_concept"),
                                Concept.is_classification_expr().label("classification_concept"),
                                Concept.is_valid_expr().label("is_active"),
                                Concept_Synonym.concept_synonym_name,
                            )
                            .join(Concept_Synonym, Concept_Synonym.concept_id == Concept.concept_id)
                            .where(func.lower(Concept_Synonym.concept_synonym_name) == q.lower()),
                            domain=domain,
                            vocabulary_id=vocabulary_id,
                            standard_only=standard_only,
                            active_only=active_only,
                            parent_ids=parent_ids,
                        ).limit(remaining)

                        if seen_ids:
                            syn_stmt = syn_stmt.where(Concept.concept_id.not_in(list(seen_ids)))

                        for row in session.execute(syn_stmt).all():
                            results.append(_row_to_match(row, "synonym", row.concept_synonym_name, None))

        except GroundworkersError:
            raise
        except Exception as exc:
            raise GroundworkersError(
                "QUERY_ERROR", f"search_exact failed with {type(exc).__name__}."
            ) from exc

        return results

    # ------------------------------------------------------------------
    # search_normalized
    # ------------------------------------------------------------------

    def search_normalized(
        self,
        query: str,
        *,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        active_only: bool = False,
        include_synonyms: bool = False,
        normalization_profile: str = "verbatim",
        parent_ids: list[int] | None = None,
        remove_stop_phrases: bool = True,
        limit: int = 20,
    ) -> list[ConceptMatch]:
        """Deterministic near-verbatim search after text normalization.

        Both the query and candidate text are normalized before comparison.
        Distinct from full-text search: deterministic equality, not ranked retrieval.
        """
        normalized_query, _steps = normalize_text_for_matching(
            query,
            profile=normalization_profile,
            remove_stop_phrases=remove_stop_phrases,
        )
        if not normalized_query:
            raise ValueError("query must be a non-empty string after normalization")

        results: list[ConceptMatch] = []
        seen_ids: set[int] = set()

        try:
            name_expr = _normalized_sql_expr(
                Concept.concept_name,
                normalization_profile=normalization_profile,
                remove_stop_phrases=remove_stop_phrases,
            )

            with self._cdm.session() as session:
                name_stmt = self._apply_concept_filters(
                    select(
                        Concept.concept_id,
                        Concept.concept_name,
                        Concept.concept_code,
                        Concept.vocabulary_id,
                        Concept.domain_id,
                        Concept.concept_class_id,
                        Concept.is_standard_expr().label("standard_concept"),
                        Concept.is_classification_expr().label("classification_concept"),
                        Concept.is_valid_expr().label("is_active"),
                    ).where(name_expr == normalized_query),
                    domain=domain,
                    vocabulary_id=vocabulary_id,
                    standard_only=standard_only,
                    active_only=active_only,
                    parent_ids=parent_ids,
                ).limit(limit)

                for row in session.execute(name_stmt).all():
                    seen_ids.add(int(row.concept_id))
                    results.append(_row_to_match(row, "name", None, None))

                if include_synonyms:
                    remaining = limit - len(results)
                    if remaining > 0:
                        syn_expr = _normalized_sql_expr(
                            Concept_Synonym.concept_synonym_name,
                            normalization_profile=normalization_profile,
                            remove_stop_phrases=remove_stop_phrases,
                        )
                        syn_stmt = self._apply_concept_filters(
                            select(
                                Concept.concept_id,
                                Concept.concept_name,
                                Concept.concept_code,
                                Concept.vocabulary_id,
                                Concept.domain_id,
                                Concept.concept_class_id,
                                Concept.is_standard_expr().label("standard_concept"),
                                Concept.is_classification_expr().label("classification_concept"),
                                Concept.is_valid_expr().label("is_active"),
                                Concept_Synonym.concept_synonym_name,
                            )
                            .join(Concept_Synonym, Concept_Synonym.concept_id == Concept.concept_id)
                            .where(syn_expr == normalized_query),
                            domain=domain,
                            vocabulary_id=vocabulary_id,
                            standard_only=standard_only,
                            active_only=active_only,
                            parent_ids=parent_ids,
                        ).limit(remaining)

                        if seen_ids:
                            syn_stmt = syn_stmt.where(Concept.concept_id.not_in(list(seen_ids)))

                        for row in session.execute(syn_stmt).all():
                            results.append(_row_to_match(row, "synonym", row.concept_synonym_name, None))

        except GroundworkersError:
            raise
        except Exception as exc:
            raise GroundworkersError(
                "QUERY_ERROR", f"search_normalized failed with {type(exc).__name__}."
            ) from exc

        return results

    # ------------------------------------------------------------------
    # search_fulltext
    # ------------------------------------------------------------------

    def search_fulltext(
        self,
        query: str,
        *,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        active_only: bool = False,
        include_synonyms: bool = True,
        parent_ids: list[int] | None = None,
        min_rank: float = 0.0,
        limit: int = 20,
    ) -> tuple[list[ConceptMatch], bool]:
        """PostgreSQL FTS match using the tsvector sidecar column (GIN-indexed).

        Returns (results, fts_available). When fts_available is False the sidecar
        column was not detected and results is always []; the caller should fall
        through to another search strategy.

        ts_rank is included in each result so the caller can apply its own quality
        threshold. Synonym FTS is included when the synonym sidecar column is also
        present; otherwise synonym results are silently omitted.
        """
        self._detect_fts_sidecars()
        if not self._fts_name_sidecar:
            return [], False

        q = query.strip()
        if not q:
            raise ValueError("query must be a non-empty string")

        results: list[ConceptMatch] = []
        seen_ids: set[int] = set()

        try:
            tsquery = func.plainto_tsquery("english", q)
            name_rank = func.ts_rank(sa_col("concept_name_tsvector"), tsquery)

            with self._cdm.session() as session:
                name_stmt = self._apply_concept_filters(
                    select(
                        Concept.concept_id,
                        Concept.concept_name,
                        Concept.concept_code,
                        Concept.vocabulary_id,
                        Concept.domain_id,
                        Concept.concept_class_id,
                        Concept.is_standard_expr().label("standard_concept"),
                        Concept.is_classification_expr().label("classification_concept"),
                        Concept.is_valid_expr().label("is_active"),
                        name_rank.label("ts_rank"),
                    ).where(sa_col("concept_name_tsvector").op("@@")(tsquery)),
                    domain=domain,
                    vocabulary_id=vocabulary_id,
                    standard_only=standard_only,
                    active_only=active_only,
                    parent_ids=parent_ids,
                ).order_by(name_rank.desc()).limit(limit)

                if min_rank > 0.0:
                    name_stmt = name_stmt.where(name_rank >= min_rank)

                for row in session.execute(name_stmt).all():
                    seen_ids.add(int(row.concept_id))
                    results.append(_row_to_match(row, "name", None, float(row.ts_rank)))

                if include_synonyms and self._fts_synonym_sidecar:
                    remaining = limit - len(results)
                    if remaining > 0:
                        syn_rank = func.ts_rank(sa_col("concept_synonym_name_tsvector"), tsquery)
                        syn_stmt = self._apply_concept_filters(
                            select(
                                Concept.concept_id,
                                Concept.concept_name,
                                Concept.concept_code,
                                Concept.vocabulary_id,
                                Concept.domain_id,
                                Concept.concept_class_id,
                                Concept.is_standard_expr().label("standard_concept"),
                                Concept.is_classification_expr().label("classification_concept"),
                                Concept.is_valid_expr().label("is_active"),
                                Concept_Synonym.concept_synonym_name,
                                syn_rank.label("ts_rank"),
                            )
                            .join(Concept_Synonym, Concept_Synonym.concept_id == Concept.concept_id)
                            .where(sa_col("concept_synonym_name_tsvector").op("@@")(tsquery)),
                            domain=domain,
                            vocabulary_id=vocabulary_id,
                            standard_only=standard_only,
                            active_only=active_only,
                            parent_ids=parent_ids,
                        ).order_by(syn_rank.desc()).limit(remaining)

                        if min_rank > 0.0:
                            syn_stmt = syn_stmt.where(syn_rank >= min_rank)
                        if seen_ids:
                            syn_stmt = syn_stmt.where(Concept.concept_id.not_in(list(seen_ids)))

                        for row in session.execute(syn_stmt).all():
                            results.append(_row_to_match(row, "synonym", row.concept_synonym_name, float(row.ts_rank)))

        except GroundworkersError:
            raise
        except Exception as exc:
            raise GroundworkersError(
                "QUERY_ERROR", f"search_fulltext failed with {type(exc).__name__}."
            ) from exc

        results.sort(key=lambda r: r.ts_rank or 0.0, reverse=True)
        return results, True

    # ------------------------------------------------------------------
    # navigate_to_standard
    # ------------------------------------------------------------------

    def navigate_to_standard(self, concept_ids: list[int]) -> list[StandardMapping]:
        """Return standard equivalents for a list of concept_ids via "Maps to" relationship edges.

        For concept_ids that are already standard: standard_concepts = [self].
        For concept_ids with no outbound "Maps to" relationship: standard_concepts = [].
        concept_ids not found in the vocabulary are silently omitted.
        """
        if not concept_ids:
            return []

        try:
            with self._cdm.session() as session:
                source_stmt = select(
                    Concept.concept_id,
                    Concept.concept_name,
                    Concept.concept_code,
                    Concept.vocabulary_id,
                    Concept.domain_id,
                    Concept.concept_class_id,
                    Concept.is_standard_expr().label("standard_concept"),
                    Concept.is_classification_expr().label("classification_concept"),
                    Concept.is_valid_expr().label("is_active"),
                ).where(Concept.concept_id.in_(concept_ids))

                source_rows = {int(r.concept_id): r for r in session.execute(source_stmt).all()}

                non_standard_ids = [
                    cid for cid, r in source_rows.items() if not r.standard_concept
                ]

                mappings: dict[int, list[MappedConcept]] = {}
                if non_standard_ids:
                    nav_stmt = (
                        select(
                            Concept_Relationship.concept_id_1.label("source_id"),
                            Concept_Relationship.relationship_id,
                            Concept.concept_id,
                            Concept.concept_name,
                            Concept.concept_code,
                            Concept.vocabulary_id,
                            Concept.domain_id,
                            Concept.concept_class_id,
                            Concept.is_standard_expr().label("standard_concept"),
                            Concept.is_classification_expr().label("classification_concept"),
                            Concept.is_valid_expr().label("is_active"),
                        )
                        .join(Concept, Concept.concept_id == Concept_Relationship.concept_id_2)
                        .where(
                            Concept_Relationship.concept_id_1.in_(non_standard_ids),
                            Concept_Relationship.relationship_id.in_(self.IDENTITY_RELATIONSHIP_IDS),
                            Concept_Relationship.is_valid_expr(),
                            Concept.is_standard_expr(),
                        )
                    )
                    for row in session.execute(nav_stmt).all():
                        src = int(row.source_id)
                        mappings.setdefault(src, []).append(
                            MappedConcept(
                                concept=serialise_concept_view(row, detail="flags"),
                                relationship_id=row.relationship_id,
                            )
                        )

        except GroundworkersError:
            raise
        except Exception as exc:
            raise GroundworkersError(
                "QUERY_ERROR",
                f"navigate_to_standard failed with {type(exc).__name__}.",
            ) from exc

        results: list[StandardMapping] = []
        for cid in concept_ids:
            src = source_rows.get(cid)
            if src is None:
                continue
            is_standard = bool(src.standard_concept)
            if is_standard:
                targets = [
                    MappedConcept(
                        concept=serialise_concept_view(src, detail="flags"),
                        relationship_id="self",
                    )
                ]
            else:
                targets = mappings.get(cid, [])
            results.append(
                ConceptMappingResult(
                    source_concept_id=cid,
                    source_concept_name=src.concept_name,
                    source_standard_concept=is_standard,
                    source_classification_concept=bool(src.classification_concept),
                    targets=targets,
                )
            )

        return results

    def navigate_to_value(self, concept_ids: list[int]) -> list[RelatedConceptMapping]:
        """Return "Maps to value" related concepts for the given concept_ids."""
        return self._navigate_relationship(concept_ids, self.VALUE_RELATIONSHIP_IDS)

    def navigate_to_unit(self, concept_ids: list[int]) -> list[RelatedConceptMapping]:
        """Return "Maps to unit" related concepts for the given concept_ids."""
        return self._navigate_relationship(concept_ids, self.UNIT_RELATIONSHIP_IDS)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_domain(self, domain: str | None) -> str | None:
        """Canonicalise a requested domain, rejecting one that cannot exist.

        Mirrors ``ConceptGroundingService._resolve_domain`` so the search tools
        and the grounding tools agree on what a domain is and fail the same way.
        """
        if not domain:
            return None
        canonical = self._domain_names.canonical(domain)
        if canonical is None:
            raise GroundworkersError(
                "INVALID_INPUT", self._domain_names.describe_unknown(domain)
            )
        return canonical

    def _apply_concept_filters(
        self,
        stmt,
        *,
        domain: str | None,
        vocabulary_id: str | None,
        standard_only: bool,
        include_classification: bool = False,
        active_only: bool = False,
        parent_ids: list[int] | None = None,
    ):
        """Apply optional vocabulary / standardness / validity / domain / parent filters.

        Standardness, validity and vocabulary are delegated to omop-alchemy's
        ``ConceptFilter`` — the package that owns those semantics — so this
        service never writes a flag comparison of its own.

        The domain is canonicalised against the CDM ``domain`` table before the
        filter is built, so a loosely-typed ``"meas value"`` reaches
        ``ConceptFilter`` as ``"Meas Value"`` and matches its case-sensitive,
        index-friendly comparison. That replaced a local ``lower()`` comparison,
        which worked but meant this service and the grounding path resolved
        domains by different rules.

        ``parent_ids`` stays local: ``ConceptFilter`` does not model ancestry,
        and should not — the join to ``concept_ancestor`` is a graph concern,
        not a flag one.
        """
        canonical_domain = self._resolve_domain(domain)
        stmt = ConceptFilter(
            domains=(canonical_domain,) if canonical_domain else None,
            vocabularies=(vocabulary_id,) if vocabulary_id else None,
            require_standard=standard_only,
            include_classification=include_classification,
            require_active=active_only,
        ).apply(stmt)
        if parent_ids:
            stmt = stmt.where(
                Concept.concept_id.in_(
                    select(Concept_Ancestor.descendant_concept_id).where(
                        Concept_Ancestor.ancestor_concept_id.in_(parent_ids)
                    )
                )
            )
        return stmt

    def _navigate_relationship(
        self,
        concept_ids: list[int],
        relationship_ids: frozenset[str],
    ) -> list[RelatedConceptMapping]:
        if not concept_ids:
            return []

        try:
            with self._cdm.session() as session:
                source_stmt = select(
                    Concept.concept_id,
                    Concept.concept_name,
                    Concept.concept_code,
                    Concept.vocabulary_id,
                    Concept.domain_id,
                    Concept.concept_class_id,
                    Concept.is_standard_expr().label("standard_concept"),
                    Concept.is_classification_expr().label("classification_concept"),
                    Concept.is_valid_expr().label("is_active"),
                ).where(Concept.concept_id.in_(concept_ids))

                source_rows = {int(r.concept_id): r for r in session.execute(source_stmt).all()}

                related: dict[int, list[MappedConcept]] = {}
                if source_rows:
                    rel_stmt = (
                        select(
                            Concept_Relationship.concept_id_1.label("source_id"),
                            Concept_Relationship.relationship_id,
                            Concept.concept_id,
                            Concept.concept_name,
                            Concept.concept_code,
                            Concept.vocabulary_id,
                            Concept.domain_id,
                            Concept.concept_class_id,
                            Concept.is_standard_expr().label("standard_concept"),
                            Concept.is_classification_expr().label("classification_concept"),
                            Concept.is_valid_expr().label("is_active"),
                        )
                        .join(Concept, Concept.concept_id == Concept_Relationship.concept_id_2)
                        .where(
                            Concept_Relationship.concept_id_1.in_(list(source_rows.keys())),
                            Concept_Relationship.relationship_id.in_(relationship_ids),
                            Concept_Relationship.is_valid_expr(),
                        )
                    )
                    for row in session.execute(rel_stmt).all():
                        src = int(row.source_id)
                        related.setdefault(src, []).append(
                            MappedConcept(
                                concept=serialise_concept_view(row, detail="flags"),
                                relationship_id=row.relationship_id,
                            )
                        )

        except GroundworkersError:
            raise
        except Exception as exc:
            raise GroundworkersError(
                "QUERY_ERROR",
                f"relationship navigation failed with {type(exc).__name__}.",
            ) from exc

        results: list[RelatedConceptMapping] = []
        for cid in concept_ids:
            src = source_rows.get(cid)
            if src is None:
                continue
            results.append(
                ConceptMappingResult(
                    source_concept_id=cid,
                    source_concept_name=src.concept_name,
                    source_standard_concept=bool(src.standard_concept),
                    source_classification_concept=bool(src.classification_concept),
                    targets=related.get(cid, []),
                )
            )
        return results


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def serialise_concept_match(match: ConceptMatch) -> dict:
    """Serialise a ConceptMatch to a JSON-safe dict for MCP tool responses."""
    result: dict = {
        **match.concept,
        "match_source": match.match_source,
        "matched_synonym": match.matched_synonym,
    }
    if match.ts_rank is not None:
        result["ts_rank"] = round(match.ts_rank, 6)
    return result


def serialise_concept_mapping(
    mapping: ConceptMappingResult, *, targets_key: str
) -> dict:
    """Serialise a mapping result, naming the target list for the calling tool.

    One shape internally; *targets_key* keeps the wire term meaningful —
    ``standard_concepts`` for standardization, ``related_concepts`` for the
    value and unit navigations.
    """
    return {
        "source_concept_id": mapping.source_concept_id,
        "source_concept_name": mapping.source_concept_name,
        "source_standard_concept": mapping.source_standard_concept,
        "source_classification_concept": mapping.source_classification_concept,
        targets_key: [
            {**target.concept, "relationship_id": target.relationship_id}
            for target in mapping.targets
        ],
    }


def serialise_standard_mapping(mapping: ConceptMappingResult) -> dict:
    """Serialise a standardization result for MCP tool responses."""
    return serialise_concept_mapping(mapping, targets_key="standard_concepts")


def serialise_related_concept_mapping(mapping: ConceptMappingResult) -> dict:
    """Serialise a value/unit navigation result for MCP tool responses."""
    return serialise_concept_mapping(mapping, targets_key="related_concepts")


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------

_STOP_PHRASE_RE = re.compile(r"\b(?:nos|nec|nfs|unspecified|unknown|other|w/?o|w/)\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text_for_matching(
    text: str,
    *,
    profile: str = "verbatim",
    remove_stop_phrases: bool = True,
) -> tuple[str, list[str]]:
    """Normalize free text into a deterministic matching form."""
    steps: list[str] = ["strip", "lowercase", "collapse_whitespace"]
    normalised = _WHITESPACE_RE.sub(" ", text.strip().lower())

    if remove_stop_phrases:
        normalised = _STOP_PHRASE_RE.sub(" ", normalised)
        steps.append("remove_stop_phrases")

    if profile in {"verbatim", "aggressive", "drug_name"}:
        normalised = _NON_ALNUM_RE.sub(" ", normalised)
        steps.append("strip_punctuation")

    if profile == "drug_name":
        normalised = normalised.replace(" extended release ", " ")
        normalised = normalised.replace(" modified release ", " ")
        steps.append("drug_name_cleanup")

    normalised = _WHITESPACE_RE.sub(" ", normalised).strip()
    return normalised, steps


def _normalized_sql_expr(column, *, normalization_profile: str, remove_stop_phrases: bool):
    expr = func.lower(column)
    if remove_stop_phrases:
        expr = func.regexp_replace(expr, r'\m(?:nos|nec|nfs|unspecified|unknown|other|w/?o|w/)\M', " ", "g")
    expr = func.regexp_replace(expr, r"[^a-z0-9]+", " ", "g")
    if normalization_profile == "drug_name":
        expr = func.replace(expr, " extended release ", " ")
        expr = func.replace(expr, " modified release ", " ")
    expr = func.regexp_replace(expr, r"\s+", " ", "g")
    return func.btrim(expr)


def _row_to_match(row, match_source: str, matched_synonym: str | None, ts_rank: float | None) -> ConceptMatch:
    return ConceptMatch(
        concept=serialise_concept_view(row, detail="flags"),
        match_source=match_source,
        matched_synonym=matched_synonym,
        ts_rank=ts_rank,
    )
