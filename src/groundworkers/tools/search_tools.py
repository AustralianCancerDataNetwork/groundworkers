from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.vocab import (
    VocabService,
    serialise_concept_match,
    serialise_standard_mapping,
)
from groundworkers.toolsets import Toolset


def register_search_tools(
    server: GroundworkersMCPServer, vocab_service: VocabService
) -> None:
    """Register lexical search tools against the MCP server."""

    @server.tool("concept_search_exact", toolsets=(Toolset.SEARCH,))
    def concept_search_exact(
        query: Annotated[str, Field(description="Exact name or synonym to match.")],
        domain: Annotated[
            str | None, Field(description="OMOP domain_id filter.")
        ] = None,
        vocabulary_id: Annotated[
            str | None, Field(description="Exact vocabulary_id filter.")
        ] = None,
        standard_only: Annotated[
            bool, Field(description="Only standard concepts.")
        ] = False,
        include_synonyms: Annotated[
            bool, Field(description="Also match synonyms.")
        ] = True,
        limit: Annotated[int, Field(description="Maximum results.")] = 20,
    ) -> dict[str, Any]:
        """Exact, case-insensitive match on concept name or synonym. concept_candidate_bundle
        already runs this channel; call it directly only to confirm that an exact name exists.
        Returns non-standard concepts too; check standard_concept."""
        # match_source distinguishes name and synonym hits; matched_synonym retains
        # the triggering synonym text.
        if not query.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "query must be a non-empty string",
            }
        safe_limit = max(1, min(limit, 50))
        try:
            results = vocab_service.search_exact(
                query,
                domain=domain or None,
                vocabulary_id=vocabulary_id or None,
                standard_only=standard_only,
                include_synonyms=include_synonyms,
                limit=safe_limit,
            )
            return {
                "query": query.strip(),
                "results": [serialise_concept_match(r) for r in results],
            }
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_search_fulltext", toolsets=(Toolset.SEARCH,))
    def concept_search_fulltext(
        query: Annotated[str, Field(description="Words to search for.")],
        domain: Annotated[
            str | None, Field(description="OMOP domain_id filter.")
        ] = None,
        vocabulary_id: Annotated[
            str | None, Field(description="Exact vocabulary_id filter.")
        ] = None,
        standard_only: Annotated[
            bool, Field(description="Only standard concepts.")
        ] = False,
        include_synonyms: Annotated[
            bool, Field(description="Also search synonyms.")
        ] = True,
        min_rank: Annotated[
            float, Field(description="Drop results below this ts_rank.")
        ] = 0.0,
        limit: Annotated[int, Field(description="Maximum results.")] = 20,
    ) -> dict[str, Any]:
        """Ranked word-based search on concept names (PostgreSQL full text).
        concept_candidate_bundle already runs this channel; call it directly to see more
        full-text results or to set min_rank. Returns non-standard concepts too. If
        tsvector_available is false, results are always empty."""
        if not query.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "query must be a non-empty string",
            }
        if not (0.0 <= min_rank <= 1.0):
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "min_rank must be between 0.0 and 1.0",
            }
        safe_limit = max(1, min(limit, 50))
        try:
            results, fts_available = vocab_service.search_fulltext(
                query,
                domain=domain or None,
                vocabulary_id=vocabulary_id or None,
                standard_only=standard_only,
                include_synonyms=include_synonyms,
                min_rank=min_rank,
                limit=safe_limit,
            )
            return {
                "query": query.strip(),
                "tsvector_available": fts_available,
                "results": [serialise_concept_match(r) for r in results],
            }
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_navigate_to_standard", toolsets=(Toolset.MAPPING,))
    def concept_navigate_to_standard(
        concept_ids: Annotated[
            list[int], Field(description="List of concept_ids to resolve.")
        ],
    ) -> dict[str, Any]:
        """Resolve concept_ids (for example non-standard search hits) to their standard equivalents
        via Maps to, in one call. Standard concepts return themselves; unmapped concepts return
        []; unknown ids are omitted."""
        if not concept_ids:
            return {"results": []}
        if len(concept_ids) > 100:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_ids must contain at most 100 entries",
            }
        if any(cid <= 0 for cid in concept_ids):
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "all concept_ids must be positive integers",
            }
        try:
            mappings = vocab_service.navigate_to_standard(concept_ids)
            return {"results": [serialise_standard_mapping(m) for m in mappings]}
        except GroundworkersError as exc:
            return exc.to_dict()
