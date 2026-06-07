from __future__ import annotations

import re
from dataclasses import dataclass, field

from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
    Concept_Synonym,
)
from sqlalchemy import column as sa_col
from sqlalchemy import func, inspect as sa_inspect, select

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.base.errors import GroundworkersError


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class ConceptMatch:
    """A single candidate returned by search_exact, search_normalized, or search_fulltext."""
    concept_id: int
    concept_name: str
    concept_code: str
    vocabulary_id: str
    domain_id: str
    concept_class_id: str
    standard_concept: bool
    invalid_reason: str | None
    match_source: str           # "name" | "synonym"
    matched_synonym: str | None = None
    ts_rank: float | None = None


@dataclass
class MappedConcept:
    """A standard concept that a source concept maps to."""
    concept_id: int
    concept_name: str
    vocabulary_id: str
    domain_id: str
    concept_class_id: str
    relationship_id: str        # e.g. "Maps to" or "self" when already standard


@dataclass
class StandardMapping:
    """Navigation result for a single source concept_id."""
    source_concept_id: int
    source_concept_name: str
    source_standard_concept: bool
    standard_concepts: list[MappedConcept] = field(default_factory=list)


@dataclass
class RelatedConceptMapping:
    """Relationship-driven mapping result for a single source concept_id."""
    source_concept_id: int
    source_concept_name: str
    source_standard_concept: bool
    related_concepts: list[MappedConcept] = field(default_factory=list)


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
        self._fts_name_sidecar: bool | None = None
        self._fts_synonym_sidecar: bool | None = None

    # ------------------------------------------------------------------
    # FTS sidecar detection
    # ------------------------------------------------------------------

    def _detect_fts_sidecars(self) -> None:
        """Detect and cache tsvector sidecar column presence (runs once)."""
        if self._fts_name_sidecar is not None:
            return
        try:
            inspector = sa_inspect(self._cdm.engine)
            concept_cols = {c["name"] for c in inspector.get_columns("concept")}
            synonym_cols = {c["name"] for c in inspector.get_columns("concept_synonym")}
            self._fts_name_sidecar = "concept_name_tsvector" in concept_cols
            self._fts_synonym_sidecar = "concept_synonym_name_tsvector" in synonym_cols
        except Exception:
            self._fts_name_sidecar = False
            self._fts_synonym_sidecar = False

    @property
    def fts_available(self) -> bool:
        """True when the concept_name_tsvector sidecar column is present."""
        self._detect_fts_sidecars()
        return bool(self._fts_name_sidecar)

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
                        Concept.standard_concept,
                        Concept.invalid_reason,
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
                                Concept.standard_concept,
                                Concept.invalid_reason,
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
            raise GroundworkersError("QUERY_ERROR", f"search_exact failed: {exc}") from exc

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
                        Concept.standard_concept,
                        Concept.invalid_reason,
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
                                Concept.standard_concept,
                                Concept.invalid_reason,
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
            raise GroundworkersError("QUERY_ERROR", f"search_normalized failed: {exc}") from exc

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
                        Concept.standard_concept,
                        Concept.invalid_reason,
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
                                Concept.standard_concept,
                                Concept.invalid_reason,
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
            raise GroundworkersError("QUERY_ERROR", f"search_fulltext failed: {exc}") from exc

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
                    Concept.vocabulary_id,
                    Concept.domain_id,
                    Concept.concept_class_id,
                    Concept.standard_concept,
                ).where(Concept.concept_id.in_(concept_ids))

                source_rows = {int(r.concept_id): r for r in session.execute(source_stmt).all()}

                non_standard_ids = [
                    cid for cid, r in source_rows.items() if r.standard_concept != "S"
                ]

                mappings: dict[int, list[MappedConcept]] = {}
                if non_standard_ids:
                    nav_stmt = (
                        select(
                            Concept_Relationship.concept_id_1.label("source_id"),
                            Concept_Relationship.relationship_id,
                            Concept.concept_id,
                            Concept.concept_name,
                            Concept.vocabulary_id,
                            Concept.domain_id,
                            Concept.concept_class_id,
                        )
                        .join(Concept, Concept.concept_id == Concept_Relationship.concept_id_2)
                        .where(
                            Concept_Relationship.concept_id_1.in_(non_standard_ids),
                            Concept_Relationship.relationship_id.in_(self.IDENTITY_RELATIONSHIP_IDS),
                            Concept_Relationship.invalid_reason.is_(None),
                            Concept.standard_concept == "S",
                        )
                    )
                    for row in session.execute(nav_stmt).all():
                        src = int(row.source_id)
                        mappings.setdefault(src, []).append(
                            MappedConcept(
                                concept_id=int(row.concept_id),
                                concept_name=row.concept_name,
                                vocabulary_id=row.vocabulary_id,
                                domain_id=row.domain_id,
                                concept_class_id=row.concept_class_id,
                                relationship_id=row.relationship_id,
                            )
                        )

        except GroundworkersError:
            raise
        except Exception as exc:
            raise GroundworkersError("QUERY_ERROR", f"navigate_to_standard failed: {exc}") from exc

        results: list[StandardMapping] = []
        for cid in concept_ids:
            src = source_rows.get(cid)
            if src is None:
                continue
            is_standard = src.standard_concept == "S"
            if is_standard:
                standard_concepts = [
                    MappedConcept(
                        concept_id=int(src.concept_id),
                        concept_name=src.concept_name,
                        vocabulary_id=src.vocabulary_id,
                        domain_id=src.domain_id,
                        concept_class_id=src.concept_class_id,
                        relationship_id="self",
                    )
                ]
            else:
                standard_concepts = mappings.get(cid, [])
            results.append(
                StandardMapping(
                    source_concept_id=cid,
                    source_concept_name=src.concept_name,
                    source_standard_concept=is_standard,
                    standard_concepts=standard_concepts,
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

    @staticmethod
    def _apply_concept_filters(
        stmt,
        *,
        domain: str | None,
        vocabulary_id: str | None,
        standard_only: bool,
        active_only: bool = False,
        parent_ids: list[int] | None = None,
    ):
        """Apply optional domain / vocabulary / standard_concept / active / parent WHERE clauses."""
        if standard_only:
            stmt = stmt.where(Concept.standard_concept == "S")
        if active_only:
            stmt = stmt.where(Concept.invalid_reason.is_(None))
        if domain:
            stmt = stmt.where(func.lower(Concept.domain_id) == domain.lower())
        if vocabulary_id:
            stmt = stmt.where(Concept.vocabulary_id == vocabulary_id)
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
                    Concept.vocabulary_id,
                    Concept.domain_id,
                    Concept.concept_class_id,
                    Concept.standard_concept,
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
                            Concept.vocabulary_id,
                            Concept.domain_id,
                            Concept.concept_class_id,
                        )
                        .join(Concept, Concept.concept_id == Concept_Relationship.concept_id_2)
                        .where(
                            Concept_Relationship.concept_id_1.in_(list(source_rows.keys())),
                            Concept_Relationship.relationship_id.in_(relationship_ids),
                            Concept_Relationship.invalid_reason.is_(None),
                        )
                    )
                    for row in session.execute(rel_stmt).all():
                        src = int(row.source_id)
                        related.setdefault(src, []).append(
                            MappedConcept(
                                concept_id=int(row.concept_id),
                                concept_name=row.concept_name,
                                vocabulary_id=row.vocabulary_id,
                                domain_id=row.domain_id,
                                concept_class_id=row.concept_class_id,
                                relationship_id=row.relationship_id,
                            )
                        )

        except GroundworkersError:
            raise
        except Exception as exc:
            raise GroundworkersError("QUERY_ERROR", f"relationship navigation failed: {exc}") from exc

        results: list[RelatedConceptMapping] = []
        for cid in concept_ids:
            src = source_rows.get(cid)
            if src is None:
                continue
            results.append(
                RelatedConceptMapping(
                    source_concept_id=cid,
                    source_concept_name=src.concept_name,
                    source_standard_concept=src.standard_concept == "S",
                    related_concepts=related.get(cid, []),
                )
            )
        return results


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def serialise_concept_match(match: ConceptMatch) -> dict:
    """Serialise a ConceptMatch to a JSON-safe dict for MCP tool responses."""
    result: dict = {
        "concept_id": match.concept_id,
        "concept_name": match.concept_name,
        "concept_code": match.concept_code,
        "vocabulary_id": match.vocabulary_id,
        "domain_id": match.domain_id,
        "concept_class_id": match.concept_class_id,
        "standard_concept": match.standard_concept,
        "invalid_reason": match.invalid_reason,
        "match_source": match.match_source,
        "matched_synonym": match.matched_synonym,
    }
    if match.ts_rank is not None:
        result["ts_rank"] = round(match.ts_rank, 6)
    return result


def serialise_standard_mapping(mapping: StandardMapping) -> dict:
    """Serialise a StandardMapping to a JSON-safe dict for MCP tool responses."""
    return {
        "source_concept_id": mapping.source_concept_id,
        "source_concept_name": mapping.source_concept_name,
        "source_standard_concept": mapping.source_standard_concept,
        "standard_concepts": [
            {
                "concept_id": sc.concept_id,
                "concept_name": sc.concept_name,
                "vocabulary_id": sc.vocabulary_id,
                "domain_id": sc.domain_id,
                "concept_class_id": sc.concept_class_id,
                "relationship_id": sc.relationship_id,
            }
            for sc in mapping.standard_concepts
        ],
    }


def serialise_related_concept_mapping(mapping: RelatedConceptMapping) -> dict:
    """Serialise a RelatedConceptMapping to a JSON-safe dict for MCP tool responses."""
    return {
        "source_concept_id": mapping.source_concept_id,
        "source_concept_name": mapping.source_concept_name,
        "source_standard_concept": mapping.source_standard_concept,
        "related_concepts": [
            {
                "concept_id": sc.concept_id,
                "concept_name": sc.concept_name,
                "vocabulary_id": sc.vocabulary_id,
                "domain_id": sc.domain_id,
                "concept_class_id": sc.concept_class_id,
                "relationship_id": sc.relationship_id,
            }
            for sc in mapping.related_concepts
        ],
    }


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
        concept_id=int(row.concept_id),
        concept_name=row.concept_name,
        concept_code=row.concept_code,
        vocabulary_id=row.vocabulary_id,
        domain_id=row.domain_id,
        concept_class_id=row.concept_class_id,
        standard_concept=row.standard_concept == "S",
        invalid_reason=row.invalid_reason,
        match_source=match_source,
        matched_synonym=matched_synonym,
        ts_rank=ts_rank,
    )
