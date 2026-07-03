from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from omop_graph.extensions.omop_alchemy import PredicateKind
from omop_graph.reasoning.grounding import GroundingConstraints
from omop_graph.reasoning.resolvers.resolvers import FullTextResolver, FullTextSynonymResolver

from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError

__all__ = ["GraphService", "GroundingPlan"]

logger = logging.getLogger(__name__)


def _short_text(value: str, *, limit: int = 120) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit - 3]}..."


@dataclass(frozen=True)
class GroundingPlan:
    query: str
    limit: int
    constraints: GroundingConstraints
    tiers: tuple[tuple[Any, ...], ...]
    min_fulltext_overlap: float = 0.0


class GraphService:
    """Caller-facing graph domain logic backed by the omop-graph runtime.

    This service owns multi-step orchestration — hierarchy walks, edge and path
    assembly, neighbourhood shaping, standard-mapping, and grounding tier
    selection — composing the normalized primitives exposed by
    ``OmopGraphAdapter``. It never touches omop-graph internals directly; the
    adapter owns the backend and returns plain dicts/tuples.
    """

    # Predicate-kind presets for equivalency path queries.
    _IDENTITY_KINDS: frozenset = frozenset({PredicateKind.IDENTITY})
    _IDENTITY_AND_HIERARCHY_KINDS: frozenset = frozenset({PredicateKind.IDENTITY, PredicateKind.HIERARCHY})

    def __init__(self, adapter: OmopGraphAdapter) -> None:
        self._adapter = adapter

    # ------------------------------------------------------------------
    # Primitive pass-throughs (single-operation, normalized by the adapter)
    # ------------------------------------------------------------------

    def get_concept(self, concept_id: int) -> dict[str, Any] | None:
        return self._adapter.get_concept(concept_id)

    def get_concept_by_code(self, vocabulary_id: str, code: str) -> list[dict[str, Any]]:
        return self._adapter.get_concept_by_code(vocabulary_id, code)

    def concept_views(self, concept_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        """Batch-fetch normalized concept views keyed by concept_id.

        Thin passthrough to the adapter's batch lookup. Returns an empty dict
        for unknown ids or on backend failure (the adapter swallows enrichment
        errors), so callers can treat a missing key as "unknown concept".
        """
        return self._adapter.concept_views(concept_ids)

    def canonicalize_domain(self, domain: str | None) -> str | None:
        return self._adapter.canonicalize_domain(domain)

    @property
    def embedding_resolver_active(self) -> bool:
        return self._adapter.embedding_resolver_active

    # ------------------------------------------------------------------
    # Hierarchy traversal
    # ------------------------------------------------------------------

    def get_ancestors(self, concept_id: int, max_depth: int) -> list[dict[str, Any]]:
        if self._adapter.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        queue: deque[tuple[int, int]] = deque(
            (parent_id, 1) for parent_id in self._adapter.parents(concept_id)
        )
        return self._walk_hierarchy(queue=queue, neighbour_getter=self._adapter.parents, max_depth=max_depth)

    def get_descendants(self, concept_id: int, max_depth: int) -> list[dict[str, Any]]:
        if self._adapter.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        queue: deque[tuple[int, int]] = deque(
            (child_id, 1) for child_id in self._adapter.children(concept_id)
        )
        return self._walk_hierarchy(queue=queue, neighbour_getter=self._adapter.children, max_depth=max_depth)

    def _walk_hierarchy(
        self,
        *,
        queue: deque[tuple[int, int]],
        neighbour_getter: Callable[[int], tuple[int, ...]],
        max_depth: int,
    ) -> list[dict[str, Any]]:
        # BFS to discover each reachable concept and its shallowest depth, then
        # batch-fetch views once (rather than one lookup per node).
        depth_by_id: dict[int, int] = {}
        while queue:
            current_id, depth = queue.popleft()
            if current_id in depth_by_id or depth > max_depth:
                continue
            depth_by_id[current_id] = depth
            if depth < max_depth:
                for next_id in neighbour_getter(current_id):
                    if next_id not in depth_by_id:
                        queue.append((int(next_id), depth + 1))

        views = self._adapter.concept_views(tuple(depth_by_id))
        results: list[dict[str, Any]] = []
        for concept_id, depth in depth_by_id.items():
            view = views.get(concept_id)
            if view is None:
                continue
            results.append(
                {
                    "concept_id": view["concept_id"],
                    "concept_name": view["concept_name"],
                    "vocabulary_id": view["vocabulary_id"],
                    "domain_id": view["domain_id"],
                    "standard_concept": view["standard_concept"],
                    "depth": depth,
                }
            )
        results.sort(key=lambda item: (item["depth"], item["concept_id"]))
        return results

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def get_edges(self, concept_id: int) -> dict[str, Any]:
        if self._adapter.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        outbound = self._adapter.edges(concept_id, direction="out", active_only=False)
        inbound = self._adapter.edges(concept_id, direction="in", active_only=False)

        other_ids = tuple(dict.fromkeys(
            [e["object_id"] for e in outbound] + [e["subject_id"] for e in inbound]
        ))
        views = self._adapter.concept_views(other_ids)

        return {
            "outbound": [
                {
                    "relationship_id": e["predicate_id"],
                    "predicate_kind": e["predicate_kind"],
                    "target_concept_id": e["object_id"],
                    "target_concept_name": self._name_of(views, e["object_id"]),
                    "valid": e["valid"],
                }
                for e in outbound
            ],
            "inbound": [
                {
                    "relationship_id": e["predicate_id"],
                    "predicate_kind": e["predicate_kind"],
                    "source_concept_id": e["subject_id"],
                    "source_concept_name": self._name_of(views, e["subject_id"]),
                    "valid": e["valid"],
                }
                for e in inbound
            ],
        }

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def find_path(
        self,
        source_id: int,
        target_id: int,
        max_depth: int,
        predicate_kinds: frozenset | None = None,
        within_domain: bool = True,
    ) -> dict[str, Any]:
        if self._adapter.get_concept(source_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {source_id} was not found")
        if source_id == target_id:
            return {"found": True, "paths": [{"length": 0, "steps": []}]}
        if self._adapter.get_concept(target_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {target_id} was not found")

        raw_paths = self._adapter.shortest_paths(
            source_id,
            target_id,
            max_depth=max_depth,
            predicate_kinds=predicate_kinds,
            within_domain=within_domain,
        )
        if not raw_paths:
            return {"found": False, "paths": []}

        all_concept_ids: set[int] = set()
        for steps in raw_paths:
            for step in steps:
                all_concept_ids.add(step["subject_id"])
                all_concept_ids.add(step["object_id"])
        views = self._adapter.concept_views(tuple(all_concept_ids))

        serialised: list[dict[str, Any]] = []
        for steps in sorted(raw_paths, key=len):
            serialised_steps = [
                {
                    "subject_id": step["subject_id"],
                    "subject_name": self._name_of(views, step["subject_id"]),
                    "predicate": step["predicate"],
                    "predicate_kind": step["predicate_kind"],
                    "object_id": step["object_id"],
                    "object_name": self._name_of(views, step["object_id"]),
                }
                for step in steps
            ]
            serialised.append({"length": len(serialised_steps), "steps": serialised_steps})
        return {"found": True, "paths": serialised}

    def find_equivalency_path(
        self,
        source_id: int,
        target_id: int,
        max_depth: int,
        allow_hierarchical_traversal: bool = False,
    ) -> dict[str, Any]:
        kinds = self._IDENTITY_AND_HIERARCHY_KINDS if allow_hierarchical_traversal else self._IDENTITY_KINDS
        return self.find_path(
            source_id=source_id,
            target_id=target_id,
            max_depth=max_depth,
            predicate_kinds=kinds,
            within_domain=False,
        )

    # ------------------------------------------------------------------
    # Standard mapping
    # ------------------------------------------------------------------

    def map_to_standard(self, vocabulary_id: str, code: str) -> dict[str, Any]:
        source_list = self._adapter.get_concept_by_code(vocabulary_id, code)
        if not source_list:
            raise GroundworkersError("NOT_FOUND", f"Concept {vocabulary_id}:{code} was not found")
        source = source_list[0]

        if source["standard_concept"]:
            return {"source": source, "standard_concepts": [source]}

        edges = self._adapter.edges(
            source["concept_id"],
            direction="out",
            predicate_kinds=self._IDENTITY_KINDS,
            active_only=True,
        )
        target_ids = tuple(dict.fromkeys(e["object_id"] for e in edges))
        views = self._adapter.concept_views(target_ids)
        standard_concepts = [
            views[target_id]
            for target_id in target_ids
            if views.get(target_id) and views[target_id]["standard_concept"]
        ]
        return {"source": source, "standard_concepts": standard_concepts}

    # ------------------------------------------------------------------
    # Neighbourhood
    # ------------------------------------------------------------------

    def get_neighbors(
        self,
        concept_id: int,
        max_depth: int,
        predicate_kinds: list[str] | None,
        max_nodes: int,
        include_edges: bool,
    ) -> dict[str, Any]:
        if self._adapter.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")

        data = self._adapter.traverse_neighborhood(
            concept_id,
            predicate_kind_names=predicate_kinds,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        views = self._adapter.concept_views(data["neighbor_ids"])

        neighbors: list[dict[str, Any]] = []
        for nid in data["neighbor_ids"]:
            view = views.get(nid)
            if view:
                neighbors.append({
                    "concept_id": view["concept_id"],
                    "concept_name": view["concept_name"],
                    "vocabulary_id": view["vocabulary_id"],
                    "domain_id": view["domain_id"],
                    "concept_class_id": view["concept_class_id"],
                    "standard_concept": view["standard_concept"],
                })

        terminated_reason = data["terminated_reason"]
        return {
            "concept_id": concept_id,
            "neighbor_count": len(neighbors),
            "edge_count": data["edge_count"],
            "neighbors": neighbors,
            "edges": list(data["edges"]) if include_edges else [],
            "terminated_early": terminated_reason is not None,
            "terminated_reason": terminated_reason,
        }

    # ------------------------------------------------------------------
    # Grounding
    # ------------------------------------------------------------------

    def ground_with_plan(self, request: GroundingPlan) -> dict[str, Any]:
        """Execute a caller-supplied grounding plan: run tiers in order, keep the
        first tier that yields (FTS-overlap filtered) hits, then enrich and shape."""
        overall_started = time.perf_counter()
        logger.info(
            "concept_ground plan query=%r parent_ids=%s tiers=%s",
            _short_text(request.query),
            list(request.constraints.parent_ids) if request.constraints.parent_ids is not None else None,
            ["+".join(type(r).__name__ for r in tier) for tier in request.tiers],
        )

        results: list[dict[str, Any]] = []
        for tier in request.tiers:
            tier_started = time.perf_counter()
            tier_name = "+".join(type(r).__name__ for r in tier)
            is_fts_tier = any(isinstance(r, (FullTextResolver, FullTextSynonymResolver)) for r in tier)
            hits = self._adapter.run_ground_tier(
                tier, request.query, constraints=request.constraints, limit=request.limit
            )
            # Drop FTS hits where fewer than min_fulltext_overlap of the query tokens
            # appear in the matched concept name, then fall through to a higher-quality tier.
            if hits and is_fts_tier and request.min_fulltext_overlap > 0.0:
                query_tokens = set(request.query.lower().split())
                results = [
                    h for h in hits
                    if self._fts_overlap(query_tokens, h["matched_label"] or "") >= request.min_fulltext_overlap
                ]
            else:
                results = list(hits)
            logger.info(
                "concept_ground tier done query=%r tier=%s duration_ms=%.1f raw=%d kept=%d",
                _short_text(request.query), tier_name,
                (time.perf_counter() - tier_started) * 1000.0, len(hits), len(results),
            )
            if results:
                break

        concept_ids = tuple(dict.fromkeys(h["concept_id"] for h in results))
        views = self._adapter.concept_views(concept_ids)

        matched_tier = results[0]["match_kind"] if results else None
        used_embedding = any(h["embedding_score"] is not None for h in results)
        payload = {
            "results": [self._merge_ground_view(h, views.get(h["concept_id"])) for h in results],
            "matched_tier": matched_tier,
            "used_embedding": used_embedding,
        }
        logger.info(
            "concept_ground complete query=%r duration_ms=%.1f matched_tier=%r used_embedding=%s result_count=%d",
            _short_text(request.query),
            (time.perf_counter() - overall_started) * 1000.0,
            matched_tier, used_embedding, len(payload["results"]),
        )
        return payload

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _name_of(views: dict[int, dict[str, Any]], concept_id: int) -> str | None:
        view = views.get(concept_id)
        return view["concept_name"] if view else None

    @staticmethod
    def _fts_overlap(query_tokens: set[str], concept_label: str) -> float:
        if not query_tokens:
            return 1.0
        label_tokens = set(concept_label.lower().split())
        return len(query_tokens & label_tokens) / len(query_tokens)

    @staticmethod
    def _merge_ground_view(hit: dict[str, Any], view: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "concept_id": hit["concept_id"],
            "concept_name": hit["concept_name"],
            "vocabulary_id": view["vocabulary_id"] if view else None,
            "domain_id": view["domain_id"] if view else None,
            "concept_class_id": view["concept_class_id"] if view else None,
            "standard_concept": True,
            "match_kind": hit["match_kind"],
            "matched_label": hit["matched_label"],
            "total_score": hit["total_score"],
            "relevance": hit["relevance"],
            "parsimony_penalty": hit["parsimony_penalty"],
            "broadness_bonus": hit["broadness_bonus"],
            "embedding_score": hit["embedding_score"],
            "separation": hit["separation"],
            "standardized_from": hit["standardized_from"],
        }
