"""
Direct ORM-backed vocabulary query primitives for agent-composable concept grounding.

EXTRACTION NOTE
---------------
This module is a stop-gap implementation inside groundworkers while related changes
settle in omop-graph (open PRs in flight). It is deliberately written with zero
groundworkers-specific dependencies so it can be extracted to omop-graph (suggested
path: omop_graph/graph/search.py) or a standalone omop-search package with
minimal friction.

Extraction checklist:
  [ ] No imports from groundworkers.* (verified — none exist)
  [ ] OmopVocabError: replace with the target package's exception type,
      or retain as a thin domain exception and re-export from the package root
  [ ] No MCP protocol concerns (error codes, tool names, server wiring) in here
  [ ] Move file; add to target package __init__.py exports
  [ ] Remove this docstring block

Context: omop_graph.reasoning.concept_handlers.concept_helpers.standardise_ids
raises NotImplementedError — the navigate_to_standard method here fills that gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Relationship,
    Concept_Synonym,
)
from sqlalchemy import column as sa_col
from sqlalchemy import func, inspect as sa_inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


# ---------------------------------------------------------------------------
# Domain exception — no dependency on groundworkers error types
# ---------------------------------------------------------------------------

class OmopVocabError(Exception):
    """Raised by OmopVocabAdapter for query or backend errors.

    Callers (e.g. MCP tool registrations) are responsible for wrapping this
    into their own error representation. This class intentionally has no
    knowledge of GroundworkersError or any MCP protocol type.
    """


# ---------------------------------------------------------------------------
# Return-type dataclasses — portable; no MCP types
# ---------------------------------------------------------------------------

@dataclass
class ConceptMatch:
    """A single candidate returned by search_exact or search_fulltext."""
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
    ts_rank: float | None = None  # populated only by search_fulltext


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


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OmopVocabAdapter:
    """
    Vocabulary query primitives backed directly by omop-alchemy ORM queries.

    These are the low-level operations that an agent (or a grounding pipeline)
    can compose to find and navigate OMOP standard concepts.  They deliberately
    expose raw quality signals (ts_rank, standard_concept flag) rather than
    making quality decisions internally — that is the caller's responsibility.

    Three operations:
      search_exact       — case-insensitive exact name / synonym match
      search_fulltext    — PostgreSQL FTS with ts_rank exposed; graceful
                           degradation when tsvector sidecar absent
      navigate_to_standard — batch concept_id → standard equivalents via
                             "Maps to" relationship edges

    Raises OmopVocabError for database / query errors.
    Raises ValueError for invalid arguments.
    Never raises GroundworkersError.
    """

    # The OMOP relationship_id(s) that express cross-vocabulary standard mapping.
    # "Maps to" is the primary identity relationship in all Athena vocabulary releases.
    IDENTITY_RELATIONSHIP_IDS: frozenset[str] = frozenset({"Maps to"})

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(engine)
        # Sidecar column detection is lazy and cached after the first call.
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
            inspector = sa_inspect(self._engine)
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
        include_synonyms: bool = True,
        limit: int = 20,
    ) -> list[ConceptMatch]:
        """
        Case-insensitive exact match against concept_name, and optionally
        concept_synonym_name.

        standard_only defaults to False so the caller can inspect non-standard
        candidates and decide whether to navigate to their standard equivalents.

        Returns name matches before synonym matches; de-duplicates by concept_id
        so a concept that matches both name and a synonym only appears once.
        """
        q = query.strip()
        if not q:
            raise ValueError("query must be a non-empty string")

        results: list[ConceptMatch] = []
        seen_ids: set[int] = set()

        try:
            with self._session_factory() as session:
                # --- name match ---
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
                ).limit(limit)

                for row in session.execute(name_stmt).all():
                    seen_ids.add(int(row.concept_id))
                    results.append(_row_to_match(row, "name", None, None))

                # --- synonym match (de-duplicated against name hits) ---
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
                            .join(
                                Concept_Synonym,
                                Concept_Synonym.concept_id == Concept.concept_id,
                            )
                            .where(
                                func.lower(Concept_Synonym.concept_synonym_name) == q.lower(),
                            ),
                            domain=domain,
                            vocabulary_id=vocabulary_id,
                            standard_only=standard_only,
                        ).limit(remaining)

                        if seen_ids:
                            syn_stmt = syn_stmt.where(
                                Concept.concept_id.not_in(list(seen_ids))
                            )

                        for row in session.execute(syn_stmt).all():
                            results.append(
                                _row_to_match(row, "synonym", row.concept_synonym_name, None)
                            )

        except OmopVocabError:
            raise
        except Exception as exc:
            raise OmopVocabError(f"search_exact failed: {exc}") from exc

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
        include_synonyms: bool = True,
        min_rank: float = 0.0,
        limit: int = 20,
    ) -> tuple[list[ConceptMatch], bool]:
        """
        PostgreSQL FTS match using the tsvector sidecar column (GIN-indexed).

        Returns (results, fts_available). When fts_available=False the tsvector
        sidecar column was not detected and results is always []; the caller
        should fall through to another search strategy.

        ts_rank is included in each result so the caller can apply its own
        quality threshold. Synonym FTS is included when the synonym sidecar
        column is also present; otherwise synonym results are silently omitted
        (not an error).

        standard_only defaults to False — see search_exact docstring.
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

            with self._session_factory() as session:
                # --- name FTS ---
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
                    ).where(
                        sa_col("concept_name_tsvector").op("@@")(tsquery)
                    ),
                    domain=domain,
                    vocabulary_id=vocabulary_id,
                    standard_only=standard_only,
                ).order_by(name_rank.desc()).limit(limit)

                if min_rank > 0.0:
                    name_stmt = name_stmt.where(name_rank >= min_rank)

                for row in session.execute(name_stmt).all():
                    seen_ids.add(int(row.concept_id))
                    results.append(_row_to_match(row, "name", None, float(row.ts_rank)))

                # --- synonym FTS (only when sidecar present) ---
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
                            .join(
                                Concept_Synonym,
                                Concept_Synonym.concept_id == Concept.concept_id,
                            )
                            .where(
                                sa_col("concept_synonym_name_tsvector").op("@@")(tsquery)
                            ),
                            domain=domain,
                            vocabulary_id=vocabulary_id,
                            standard_only=standard_only,
                        ).order_by(syn_rank.desc()).limit(remaining)

                        if min_rank > 0.0:
                            syn_stmt = syn_stmt.where(syn_rank >= min_rank)
                        if seen_ids:
                            syn_stmt = syn_stmt.where(
                                Concept.concept_id.not_in(list(seen_ids))
                            )

                        for row in session.execute(syn_stmt).all():
                            results.append(
                                _row_to_match(
                                    row, "synonym", row.concept_synonym_name, float(row.ts_rank)
                                )
                            )

        except OmopVocabError:
            raise
        except Exception as exc:
            raise OmopVocabError(f"search_fulltext failed: {exc}") from exc

        results.sort(key=lambda r: r.ts_rank or 0.0, reverse=True)
        return results, True

    # ------------------------------------------------------------------
    # navigate_to_standard
    # ------------------------------------------------------------------

    def navigate_to_standard(
        self,
        concept_ids: list[int],
    ) -> list[StandardMapping]:
        """
        Given a list of concept_ids, return their standard equivalents via
        IDENTITY-type ("Maps to") relationship edges.

        For concept_ids that are already standard: standard_concepts = [self].
        For concept_ids with no outbound "Maps to" relationship: standard_concepts = [].
        concept_ids not found in the vocabulary are silently omitted.

        All navigation is done in two queries (one for source metadata, one batch
        join for mappings) regardless of the number of input ids.

        This fills the gap left by omop_graph.reasoning.concept_handlers.
        concept_helpers.standardise_ids, which currently raises NotImplementedError.
        """
        if not concept_ids:
            return []

        try:
            with self._session_factory() as session:
                # Query full metadata for all source concepts in one round-trip
                source_stmt = select(
                    Concept.concept_id,
                    Concept.concept_name,
                    Concept.vocabulary_id,
                    Concept.domain_id,
                    Concept.concept_class_id,
                    Concept.standard_concept,
                ).where(Concept.concept_id.in_(concept_ids))

                source_rows = {
                    int(r.concept_id): r
                    for r in session.execute(source_stmt).all()
                }

                non_standard_ids = [
                    cid for cid, r in source_rows.items()
                    if r.standard_concept != "S"
                ]

                # Batch navigate for all non-standard concepts in one query
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
                        .join(
                            Concept,
                            Concept.concept_id == Concept_Relationship.concept_id_2,
                        )
                        .where(
                            Concept_Relationship.concept_id_1.in_(non_standard_ids),
                            Concept_Relationship.relationship_id.in_(
                                self.IDENTITY_RELATIONSHIP_IDS
                            ),
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

        except OmopVocabError:
            raise
        except Exception as exc:
            raise OmopVocabError(f"navigate_to_standard failed: {exc}") from exc

        results: list[StandardMapping] = []
        for cid in concept_ids:
            src = source_rows.get(cid)
            if src is None:
                continue  # concept_id not found — silently skip

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
    ):
        """Apply optional domain / vocabulary / standard_concept WHERE clauses."""
        if standard_only:
            stmt = stmt.where(Concept.standard_concept == "S")
        if domain:
            stmt = stmt.where(func.lower(Concept.domain_id) == domain.lower())
        if vocabulary_id:
            stmt = stmt.where(Concept.vocabulary_id == vocabulary_id)
        return stmt


# ---------------------------------------------------------------------------
# Module-level serialisation helpers (used by tool registration layer)
# ---------------------------------------------------------------------------

def _row_to_match(
    row,
    match_source: str,
    matched_synonym: str | None,
    ts_rank: float | None,
) -> ConceptMatch:
    """Convert a SQLAlchemy row proxy to a ConceptMatch dataclass."""
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
