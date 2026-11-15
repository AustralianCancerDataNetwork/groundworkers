from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from pydantic import Field

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.grounding import ConceptGroundingService
from groundworkers.toolsets import Toolset

logger = logging.getLogger(__name__)


def _short_text(value: str, *, limit: int = 120) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def register_resolver_tools(
    server: GroundworkersMCPServer, grounding_service: ConceptGroundingService
) -> None:
    """Register free-text concept resolution tools against the MCP server.

    These tools map unstructured text (clinical terms, natural language, partial
    descriptions) to OMOP standard concepts. They are probabilistic — results
    are ranked candidates, not guaranteed matches.

    For deterministic lookups from known identifiers (concept_id, vocab+code,
    hierarchy traversal) see concept_tools.py.
    For direct lexical search with retrieval signals see search_tools.py.
    """

    @server.tool("concept_ground", toolsets=(Toolset.SEARCH,))
    async def concept_ground(
        query: Annotated[str, Field(description="The source term text.")],
        limit: Annotated[int, Field(description="Maximum results.")] = 5,
        domain: Annotated[
            str | None, Field(description="OMOP domain_id to restrict to, when known.")
        ] = None,
        vocabulary_id: Annotated[
            str | None, Field(description="Restrict to one exact vocabulary_id.")
        ] = None,
        parent_ids: Annotated[
            list[int] | None,
            Field(description="Keep only descendants of these concept_ids."),
        ] = None,
        standard_only: Annotated[
            bool,
            Field(
                description="Restrict candidate resolution to concepts flagged 'S' or 'C'. The default false resolves non-standard matches to standard via Maps to."
            ),
        ] = False,
        active_only: Annotated[
            bool,
            Field(
                description="Exclude deprecated or upgraded concepts from candidate resolution."
            ),
        ] = False,
        include_embedding: Annotated[
            bool, Field(description="Allow the embedding tier.")
        ] = True,
    ) -> dict[str, Any]:
        """Quick resolver: ranked candidates from the first resolver tier that matches (exact, then
        full-text, then embedding, then partial), so it can stop at a short or broad name. Use
        it for a fast lead; use concept_candidate_bundle for a broad candidate set. By default
        non-standard matches are resolved to standard via Maps to (standardized_from shows the
        source). classification_concept ('C') results are hierarchy nodes, not mapping targets."""
        # Each lexical tier also checks synonyms. Results retain total_score, relevance,
        # parsimony_penalty, broadness_bonus, separation, embedding_score and
        # standardized_from. Strict flags distinguish standard S from classification C.
        # grounding_explanation records the matched tier, embedding participation,
        # effective parent IDs and candidate standard/active policies.
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
            ground_result = await grounding_service.async_ground(
                stripped,
                limit=safe_limit,
                domain=domain or None,
                vocabulary_id=vocabulary_id or None,
                parent_ids=resolved_parent_ids,
                standard_only=standard_only,
                active_only=active_only,
                include_embedding=include_embedding,
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
