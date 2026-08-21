from __future__ import annotations

import logging
import time
from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.grounding import ConceptGroundingService

logger = logging.getLogger(__name__)


def _short_text(value: str, *, limit: int = 120) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit - 3]}..."


def register_resolver_tools(server: GroundworkersMCPServer, grounding_service: ConceptGroundingService) -> None:
    """Register free-text concept resolution tools against the MCP server.

    These tools map unstructured text (clinical terms, natural language, partial
    descriptions) to OMOP standard concepts. They are probabilistic — results
    are ranked candidates, not guaranteed matches.

    For deterministic lookups from known identifiers (concept_id, vocab+code,
    hierarchy traversal) see concept_tools.py.
    For direct lexical search with retrieval signals see search_tools.py.
    """

    @server.tool("concept_ground")
    def concept_ground(
        query: str,
        limit: int = 5,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        parent_ids: list[int] | None = None,
        standard_only: bool = False,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """Ground free text to ranked OMOP concept candidates.

        Runs a tiered resolver pipeline (Exact → FullText → Embedding → Partial)
        and short-circuits on the first tier that returns results. Each tier
        also matches against concept synonyms.

        match_kind in each result indicates which resolver tier produced it:
          EXACT             — case-insensitive exact match on concept_name or synonym
          FULLTEXT          — PostgreSQL FTS (requires tsvector sidecar column)
          EMBEDDING_NEAREST — nearest-neighbour embedding search
          PARTIAL           — iLIKE fragment match (last resort)

        Each result includes scoring fields (total_score, relevance, parsimony_penalty,
        broadness_bonus, separation, embedding_score) and standardized_from when the
        grounded concept was mapped from a non-standard source concept.

        Each result also carries strict OMOP flags: standard_concept is true only for
        raw standard_concept = 'S', and classification_concept only for 'C'. Grounding
        can land on a classification concept, which is a valid hierarchy node but not a
        valid mapping target for a CDM entity field — check the flags rather than
        assuming every result is standard.

        standard_only: when true, restrict candidate resolution to concepts carrying a
          standardness flag ('S' or 'C'). Defaults to false, so non-standard concepts
          are resolved and then standardized via "Maps to", which is usually what you
          want. Note this narrows *candidates*, not results.
        active_only: when true, exclude concepts with an invalid_reason (deprecated or
          upgraded) from candidate resolution. Defaults to false.

        grounding_explanation summarises which resolver tier matched, whether embedding
        scoring was active, which parent_ids constrained the search space, and the
        standard_only/active_only policy that was applied.

        parent_ids: optional list of OMOP concept_ids that act as required ancestors.
          Only concepts that are descendants of at least one of these will be returned.
          Use this to constrain grounding to a specific clinical sub-hierarchy — e.g.
          pass the concept_id for "Neoplastic disease" to ensure only oncology results
          are returned, or the concept_id for a specific drug class to scope a drug
          lookup to that class.
          When omitted the search runs without an ancestry constraint.

        For finer control over resolver selection and quality thresholds, use
        concept_search_exact, concept_search_fulltext, embedding_search, or
        concept_navigate_to_standard.
        """
        stripped = query.strip()
        if not stripped:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "query must be a non-empty string",
            }
        if parent_ids is not None and any(pid <= 0 for pid in parent_ids):
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "all parent_ids must be positive integers",
            }
        safe_limit = max(1, min(limit, 20))
        resolved_parent_ids = tuple(parent_ids) if parent_ids else None
        started = time.perf_counter()
        logger.info(
            "concept_ground tool start query=%r limit=%d domain=%r vocabulary_id=%r "
            "parent_ids=%s standard_only=%s active_only=%s",
            _short_text(stripped),
            safe_limit,
            domain,
            vocabulary_id,
            list(resolved_parent_ids) if resolved_parent_ids is not None else None,
            standard_only,
            active_only,
        )
        try:
            ground_result = grounding_service.ground(
                stripped,
                limit=safe_limit,
                domain=domain or None,
                vocabulary_id=vocabulary_id or None,
                parent_ids=resolved_parent_ids,
                standard_only=standard_only,
                active_only=active_only,
            )
            duration_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "concept_ground tool done query=%r duration_ms=%.1f result_count=%d explanation=%s",
                _short_text(stripped),
                duration_ms,
                len(ground_result["results"]),
                ground_result["grounding_explanation"],
            )
            return {
                "query": stripped,
                "results": ground_result["results"],
                "grounding_explanation": ground_result["grounding_explanation"],
            }
        except GroundworkersError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            logger.warning(
                "concept_ground tool failed query=%r duration_ms=%.1f code=%s message=%s",
                _short_text(stripped),
                duration_ms,
                exc.code,
                exc.message,
            )
            return exc.to_dict()
