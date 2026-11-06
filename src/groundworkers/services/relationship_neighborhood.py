"""Per-seed bounded traversal over explicit active vocabulary relationships."""

from dataclasses import dataclass, field
from typing import Any

from groundworkers.services.graph import GraphService


@dataclass
class SeedWalk:
    depths: dict[int, int]
    edges: dict[tuple[int, int, str], dict[str, Any]] = field(default_factory=dict)
    truncated: bool = False


@dataclass
class RelationshipTraversal:
    graph: GraphService
    relationship_ids: list[str]
    max_depth: int
    max_nodes: int
    max_edges: int
    edge_cache: dict[int, list[dict[str, Any]]] = field(default_factory=dict)

    def walk(self, seed: int) -> SeedWalk:
        result = SeedWalk(depths={seed: 0})
        frontier = {seed}
        for depth in range(1, self.max_depth + 1):
            next_frontier: set[int] = set()
            for current_id in sorted(frontier):
                next_frontier.update(self._expand(result, current_id, depth))
            frontier = next_frontier
            if not frontier:
                break
        return result

    def _expand(self, result: SeedWalk, current_id: int, depth: int) -> set[int]:
        if current_id not in self.edge_cache:
            self.edge_cache[current_id] = self.graph.get_relationship_edges(
                current_id, self.relationship_ids, self.max_edges + 1
            )
        rows = self.edge_cache[current_id]
        if len(rows) > self.max_edges:
            result.truncated = True
        next_frontier: set[int] = set()
        for edge in rows[: self.max_edges]:
            neighbor = edge["target_concept_id"]
            key = (current_id, neighbor, edge["relationship_id"])
            if self._admit(result, key, depth):
                next_frontier.add(neighbor)
        return next_frontier

    def _admit(self, result: SeedWalk, key: tuple[int, int, str], depth: int) -> bool:
        current, neighbor, relationship = key
        if key not in result.edges and len(result.edges) >= self.max_edges:
            result.truncated = True
            return False
        discovered = neighbor not in result.depths
        if discovered:
            if len(result.depths) >= self.max_nodes:
                result.truncated = True
                return False
            result.depths[neighbor] = depth
        result.edges[key] = {
            "subject_id": current,
            "object_id": neighbor,
            "relationship_id": relationship,
        }
        return discovered
