from __future__ import annotations

from typing import Any

from omop_alchemy.cdm.query import ConceptFilter
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
        max_depth: int = 5,
    ) -> None:
        self._graph = graph
        self._min_fulltext_overlap = min_fulltext_overlap
        self._max_depth = max_depth

    def ground(
        self,
        query: str,
        *,
        limit: int,
        domain: str | None,
        vocabulary_id: str | None,
        parent_ids: tuple[int, ...] | None = None,
        standard_only: bool = False,
        active_only: bool = False,
        include_embedding: bool = True,
    ) -> dict[str, Any]:
        stripped = query.strip()
        if not stripped:
            raise ValueError("query must be a non-empty string")

        # ConceptFilter rejects a non-positive limit in __post_init__, and the
        # candidate cap passed to omop-graph is meaningless at zero. Validate here
        # so a direct Python caller gets a Groundworkers error rather than a
        # ValueError raised from inside another package's dataclass.
        if limit <= 0:
            raise GroundworkersError(
                "INVALID_INPUT",
                f"limit must be a positive integer, got {limit}",
            )

        canonical_domain = self._resolve_domain(domain)
        search_constraint = self._build_search_constraint(
            canonical_domain,
            vocabulary_id,
            standard_only=standard_only,
            active_only=active_only,
        )
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
                    max_depth=self._max_depth,
                ),
                tiers=self._build_tier_plan(
                    query=stripped,
                    search_space_narrowed=bool(canonical_domain or vocabulary_id),
                    include_embedding=include_embedding,
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
                "standard_only": standard_only,
                "active_only": active_only,
                # Set when the embedding tier was planned but could not run, so a
                # degraded lexical-only answer is never presented as a complete one.
                "embedding_tier_detail": result.get("embedding_tier_detail"),
            },
        }

    async def async_ground(
        self,
        query: str,
        *,
        limit: int,
        domain: str | None,
        vocabulary_id: str | None,
        parent_ids: tuple[int, ...] | None = None,
        standard_only: bool = False,
        active_only: bool = False,
        include_embedding: bool = True,
    ) -> dict[str, Any]:
        """MCP-facing grounding with native async embedding resolution."""

        stripped = query.strip()
        if not stripped:
            raise ValueError("query must be a non-empty string")
        if limit <= 0:
            raise GroundworkersError(
                "INVALID_INPUT",
                f"limit must be a positive integer, got {limit}",
            )
        canonical_domain = self._resolve_domain(domain)
        search_constraint = self._build_search_constraint(
            canonical_domain,
            vocabulary_id,
            standard_only=standard_only,
            active_only=active_only,
        )
        if parent_ids is not None and not parent_ids:
            raise GroundworkersError(
                "QUERY_ERROR",
                "parent_ids was provided but empty; omit it to run unconstrained grounding",
            )
        result = await self._graph.async_ground_with_plan(
            GroundingPlan(
                query=stripped,
                limit=limit,
                constraints=GroundingConstraints(
                    parent_ids=parent_ids,
                    search_constraint=search_constraint,
                    max_depth=self._max_depth,
                ),
                tiers=self._build_tier_plan(
                    query=stripped,
                    search_space_narrowed=bool(canonical_domain or vocabulary_id),
                    include_embedding=include_embedding,
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
                "standard_only": standard_only,
                "active_only": active_only,
                "embedding_tier_detail": result.get("embedding_tier_detail"),
            },
        }

    def _resolve_domain(self, domain: str | None) -> str | None:
        """Canonicalise the requested domain, rejecting one that cannot exist.

        A domain matching no row in the CDM ``domain`` table cannot match any
        concept, so filtering on it returns zero results — indistinguishable
        from a genuine miss. Failing with the valid set is the difference
        between a confusing empty answer and a fixable message.
        """
        if domain is None:
            return None
        canonical = self._graph.canonicalize_domain(domain)
        if canonical is None:
            raise GroundworkersError(
                "INVALID_INPUT", self._graph.describe_unknown_domain(domain)
            )
        return canonical

    def _build_search_constraint(
        self,
        domain: str | None,
        vocabulary_id: str | None,
        *,
        standard_only: bool,
        active_only: bool,
    ) -> ConceptFilter | None:
        """Translate Groundworkers' grounding policy into an omop-alchemy filter.

        Every policy dimension is stated explicitly rather than left to the
        filter's defaults:

        * ``domains`` / ``vocabularies`` narrow the candidate search space.
        * ``require_standard`` restricts *candidate resolution*; result-level
          strictness is reported separately by ``GraphService``, which returns
          ``standard_concept`` and ``classification_concept`` as distinct flags.
        * ``require_active`` drops concepts whose ``invalid_reason`` is set, using
          normalized semantics that treat blank/whitespace as active.
        * ``limit`` is deliberately never set. It would become the ANN ``k`` for the
          embedding resolver and a SQL ``LIMIT`` for the lexical resolvers, changing
          candidate recall and ordering. ``ground_term``'s ``max_candidates`` remains
          the only cap, which is the pre-1.x behaviour.

        Returns ``None`` when no dimension is constrained, so an unconstrained
        request still passes no filter at all to omop-graph.
        """
        if not domain and not vocabulary_id and not standard_only and not active_only:
            return None
        return ConceptFilter(
            domains=(domain,) if domain else None,
            vocabularies=(vocabulary_id,) if vocabulary_id else None,
            require_standard=standard_only,
            include_classification=True,
            require_active=active_only,
        )

    def _build_tier_plan(
        self,
        *,
        query: str,
        search_space_narrowed: bool,
        include_embedding: bool = True,
    ) -> tuple[tuple[Any, ...], ...]:
        tiers: list[tuple[Any, ...]] = [
            (ExactLabelResolver(), ExactSynonymResolver()),
            (FullTextResolver(), FullTextSynonymResolver()),
        ]
        if include_embedding and self._graph.embedding_resolver_active:
            tiers.append((EmbeddingResolver(),))

        # Partial matching without a domain/vocabulary constraint runs ILIKE against
        # the full concept table. Only add this tier once the search space is narrowed
        # and the query is short enough to be practical. Standard-only/active-only do
        # not count as narrowing: they are non-selective over the concept table.
        max_partial_query_len = 60
        if search_space_narrowed and len(query) <= max_partial_query_len:
            tiers.append((PartialLabelResolver(), PartialSynonymResolver()))
        return tuple(tiers)
