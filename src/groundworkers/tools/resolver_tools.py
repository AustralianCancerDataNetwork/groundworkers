from __future__ import annotations

from typing import Any

from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer


def register_resolver_tools(server: GroundcrewServer, graph_adapter: OmopGraphAdapter) -> None:
    """Register free-text concept resolution tools against the MCP server.

    These tools map unstructured text (clinical terms, natural language, partial
    descriptions) to OMOP standard concepts. They are probabilistic — results
    are ranked candidates, not guaranteed matches.

    For deterministic lookups from known identifiers (concept_id, vocab+code,
    hierarchy traversal) see concept_tools.py.
    For agent-composable primitive search operations see search_tools.py.
    """

    @server.tool("concept_ground")
    def concept_ground(
        query: str,
        limit: int = 5,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        parent_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Ground free text to matching OMOP standard concepts ranked by relevance.

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

        grounding_explanation summarises which resolver tier matched, whether embedding
        scoring was active, and which parent_ids constrained the search space.

        parent_ids: optional list of OMOP concept_ids that act as required ancestors.
          Only concepts that are descendants of at least one of these will be returned.
          Use this to constrain grounding to a specific clinical sub-hierarchy — e.g.
          pass the concept_id for "Neoplastic disease" to ensure only oncology results
          are returned, or the concept_id for a specific drug class to scope a drug
          lookup to that class.
          When omitted the search is anchored to the domain root (or all known domain
          roots when domain is also omitted).

        For finer control over resolver selection and quality thresholds, use the
        agent-composable primitives: concept_search_exact, concept_search_fulltext,
        embedding_search, and concept_navigate_to_standard.
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
        try:
            ground_result = graph_adapter.ground(
                stripped, safe_limit, domain or None, vocabulary_id or None,
                parent_ids=resolved_parent_ids,
            )
            return {
                "query": stripped,
                "results": ground_result["results"],
                "grounding_explanation": ground_result["grounding_explanation"],
            }
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}
