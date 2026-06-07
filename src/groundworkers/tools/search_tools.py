from __future__ import annotations

from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services.vocab import (
    VocabService,
    serialise_concept_match,
    serialise_standard_mapping,
)


def register_search_tools(server: GroundcrewServer, vocab_service: VocabService) -> None:
    """Register agent-composable primitive search tools against the MCP server."""

    @server.tool("concept_search_exact")
    def concept_search_exact(
        query: str,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        include_synonyms: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        Case-insensitive exact match of a query string against concept_name and
        (optionally) concept_synonym_name.

        Unlike concept_ground, standard_only defaults to false — results include
        non-standard concepts so the caller can inspect the standard_concept flag
        and decide whether to call concept_navigate_to_standard.

        match_source is "name" when the concept_name matched, "synonym" when a
        concept_synonym_name matched. matched_synonym contains the synonym string
        that triggered the match.
        """
        if not query.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "query must be a non-empty string"}
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
            return {"query": query.strip(), "results": [serialise_concept_match(r) for r in results]}
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_search_fulltext")
    def concept_search_fulltext(
        query: str,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        include_synonyms: bool = True,
        min_rank: float = 0.0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        PostgreSQL full-text search against the concept_name tsvector sidecar column.

        Returns results with ts_rank exposed so the caller can apply its own quality
        threshold (e.g. discard results where ts_rank < 0.05).

        tsvector_available indicates whether the GIN-indexed sidecar column was
        detected. When false, results is always [] and the caller should fall
        through to embedding_search or concept_search_exact.

        min_rank is an optional server-side pre-filter (avoids returning very
        large result sets); the caller's own threshold may be stricter.

        standard_only defaults to false — see concept_search_exact.
        """
        if not query.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "query must be a non-empty string"}
        if not (0.0 <= min_rank <= 1.0):
            return {"error": True, "code": "INVALID_INPUT", "message": "min_rank must be between 0.0 and 1.0"}
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
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_navigate_to_standard")
    def concept_navigate_to_standard(concept_ids: list[int]) -> dict[str, Any]:
        """
        Given a list of concept_ids, return their standard OMOP equivalents by
        following "Maps to" relationship edges.

        For concepts that are already standard: standard_concepts contains the
        concept itself (relationship_id = "self").
        For concepts with no "Maps to" mapping: standard_concepts is [].
        concept_ids not found in the vocabulary are silently omitted from results.

        This is the batch-by-concept-id form of concept_map_to_standard. Use it
        after concept_search_exact, concept_search_fulltext, or embedding_search
        to resolve non-standard candidates to their standard equivalents in a
        single round-trip.
        """
        if not concept_ids:
            return {"results": []}
        if len(concept_ids) > 100:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_ids must contain at most 100 entries"}
        if any(cid <= 0 for cid in concept_ids):
            return {"error": True, "code": "INVALID_INPUT", "message": "all concept_ids must be positive integers"}
        try:
            mappings = vocab_service.navigate_to_standard(concept_ids)
            return {"results": [serialise_standard_mapping(m) for m in mappings]}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}
