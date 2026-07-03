from __future__ import annotations

from typing import Any

from omop_graph.graph.constraints import SearchConstraintConcept
from omop_graph.reasoning.grounding import GroundingConstraints
from omop_graph.reasoning.resolvers.resolvers import (
    EmbeddingResolver,
    ExactLabelResolver,
    ExactSynonymResolver,
    FullTextResolver,
    FullTextSynonymResolver,
    PartialLabelResolver,
    PartialSynonymResolver,
)

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.graph import GraphService, GroundingPlan


class ConceptGroundingService:
    """Use-case policy for free-text grounding over the OMOP graph.

    The graph service owns omop-graph execution details.
    This service owns the caller-facing grounding strategy: domain normalization,
    optional ancestry constraints, tier ordering, and response explanation.
    """

    def __init__(
        self,
        graph: GraphService,
        *,
        min_fulltext_overlap: float = 0.0,
    ) -> None:
        self._graph = graph
        self._min_fulltext_overlap = min_fulltext_overlap

    def ground(
        self,
        query: str,
        *,
        limit: int,
        domain: str | None,
        vocabulary_id: str | None,
        parent_ids: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        stripped = query.strip()
        if not stripped:
            raise ValueError("query must be a non-empty string")

        canonical_domain = self._graph.canonicalize_domain(domain)
        search_constraint = self._build_search_constraint(canonical_domain, vocabulary_id)
        if parent_ids is not None and not parent_ids:
            raise GroundworkersError(
                "QUERY_ERROR",
                "parent_ids was provided but empty; omit it to run unconstrained grounding",
            )

        result = self._graph.ground_with_plan(
            GroundingPlan(
                query=stripped,
                limit=limit,
                constraints=GroundingConstraints(
                    parent_ids=parent_ids,
                    search_constraint=search_constraint,
                ),
                tiers=self._build_tier_plan(
                    query=stripped,
                    search_constraint=search_constraint,
                ),
                min_fulltext_overlap=self._min_fulltext_overlap,
            )
        )
        return {
            "results": result["results"],
            "grounding_explanation": {
                "matched_tier": result["matched_tier"],
                "used_embedding": result["used_embedding"],
                "effective_parent_ids": list(parent_ids) if parent_ids is not None else [],
                "parent_ids_source": "explicit" if parent_ids is not None else "none",
            },
        }

    def _build_search_constraint(
        self,
        domain: str | None,
        vocabulary_id: str | None,
    ) -> SearchConstraintConcept | None:
        if not domain and not vocabulary_id:
            return None
        return SearchConstraintConcept(
            domains=(domain,) if domain else None,
            vocabularies=(vocabulary_id,) if vocabulary_id else None,
        )

    def _build_tier_plan(
        self,
        *,
        query: str,
        search_constraint: SearchConstraintConcept | None,
    ) -> tuple[tuple[Any, ...], ...]:
        tiers: list[tuple[Any, ...]] = [
            (ExactLabelResolver(), ExactSynonymResolver()),
            (FullTextResolver(), FullTextSynonymResolver()),
        ]
        if self._graph.embedding_resolver_active:
            tiers.append((EmbeddingResolver(),))

        # Partial matching without a domain/vocabulary constraint runs ILIKE against
        # the full concept table. Only add this tier once the search space is narrowed
        # and the query is short enough to be practical.
        max_partial_query_len = 60
        if search_constraint is not None and len(query) <= max_partial_query_len:
            tiers.append((PartialLabelResolver(), PartialSynonymResolver()))
        return tuple(tiers)
