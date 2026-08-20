from __future__ import annotations

from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.graph import GraphService


def register_concept_tools(server: GroundworkersMCPServer, graph_service: GraphService) -> None:
    """Register deterministic concept lookup tools against the MCP server.

    These tools take a known identifier (concept_id, vocabulary+code) and return
    a fact — they are deterministic lookups, not text matching.

    For free-text grounding see resolver_tools.py.
    For agent-composable search primitives see search_tools.py.
    """

    @server.tool("concept_get")
    def concept_get(concept_id: int) -> dict[str, Any]:
        """Returns one OMOP concept by concept_id."""
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        try:
            concept = graph_service.get_concept(concept_id)
            if concept is None:
                return {"error": True, "code": "NOT_FOUND", "message": f"Concept {concept_id} was not found"}
            return concept
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_by_code")
    def concept_by_code(vocabulary_id: str, concept_code: str) -> dict[str, Any]:
        """Returns one OMOP concept by vocabulary_id and concept_code."""
        if not vocabulary_id.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "vocabulary_id must be a non-empty string"}
        if not concept_code.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_code must be a non-empty string"}
        try:
            concepts = graph_service.get_concept_by_code(vocabulary_id, concept_code)
            if not concepts:
                return {
                    "error": True,
                    "code": "NOT_FOUND",
                    "message": f"Concept {vocabulary_id}:{concept_code} was not found",
                }
            return {"concepts": concepts}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_ancestors")
    def concept_ancestors(concept_id: int, max_depth: int = 5) -> dict[str, Any]:
        """Returns ancestor concepts for one concept_id."""
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        safe_depth = max(1, min(max_depth, 20))
        try:
            ancestors = graph_service.get_ancestors(concept_id, safe_depth)
            return {"concept_id": concept_id, "ancestors": ancestors}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_descendants")
    def concept_descendants(concept_id: int, max_depth: int = 3) -> dict[str, Any]:
        """Returns descendant concepts for one concept_id."""
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        safe_depth = max(1, min(max_depth, 10))
        try:
            descendants = graph_service.get_descendants(concept_id, safe_depth)
            return {"concept_id": concept_id, "descendants": descendants}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_relationships")
    def concept_relationships(concept_id: int) -> dict[str, Any]:
        """Returns all relationships for a concept grouped by direction (inbound/outbound)."""
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        try:
            edges = graph_service.get_edges(concept_id)
            return {"concept_id": concept_id, **edges}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_equivalency_path")
    def concept_equivalency_path(
        source_id: int,
        target_id: int,
        allow_hierarchical_traversal: bool = False,
        max_depth: int = 8,
    ) -> dict[str, Any]:
        """Returns the shortest equivalency path(s) between two OMOP concepts.

        Traverses only identity and (optionally) hierarchy relationships —
        never attribute, composition, or association edges.  Cross-domain
        edges are always included because identity relationships are designed
        to span vocabulary boundaries.

        allow_hierarchical_traversal=False (default)
          Only IDENTITY predicates: Maps to, Concept same_as, Concept poss_eq,
          Mapped from, etc.  The path represents a direct cross-vocabulary
          equivalence with no loss of specificity.

        allow_hierarchical_traversal=True
          Adds HIERARCHY predicates (Is a / Subsumes).  The path may step up
          or down the ancestry chain, so the connection found may be at a
          broader level of abstraction (e.g. source maps to an ancestor of
          target rather than target itself).
        """
        if source_id <= 0 or target_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "source_id and target_id must be positive integers"}
        safe_depth = max(2, min(max_depth, 15))
        try:
            result = graph_service.find_equivalency_path(
                source_id, target_id, safe_depth, allow_hierarchical_traversal
            )
            return {"source_id": source_id, "target_id": target_id, **result}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_path")
    def concept_path(
        source_id: int,
        target_id: int,
        max_depth: int = 8,
        within_domain: bool = True,
    ) -> dict[str, Any]:
        """Returns the shortest path(s) between two OMOP concepts across all relationship types.

        Traverses every relationship kind in the concept graph: IDENTITY,
        HIERARCHY, ATTRIBUTE, COMPOSITION, and ASSOCIATION.  Use this when
        you want to find any conceptual connection regardless of relationship
        type.

        within_domain=True (default)
          Only traverses edges where both endpoint concepts share the same
          domain_id (e.g. Condition → Condition).  Reduces noise for most
          queries.

        within_domain=False
          Allows cross-domain traversal.  Required when the path crosses
          vocabulary/domain boundaries via attribute relationships such as
          "Has asso morph", "Has finding site", or "Has associated procedure".
        """
        if source_id <= 0 or target_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "source_id and target_id must be positive integers"}
        safe_depth = max(2, min(max_depth, 15))
        try:
            result = graph_service.find_path(source_id, target_id, safe_depth, within_domain=within_domain)
            return {"source_id": source_id, "target_id": target_id, **result}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_neighbors")
    def concept_neighbors(
        concept_id: int,
        max_depth: int = 2,
        predicate_kinds: list[str] | None = None,
        max_nodes: int = 100,
        include_edges: bool = True,
    ) -> dict[str, Any]:
        """Bounded multi-hop neighborhood exploration for one concept.

        Performs a BFS from the seed concept, following outgoing relationship
        edges up to max_depth hops and collecting all reachable concepts and
        the edges connecting them.  This reaches across all relationship types
        simultaneously — hierarchy, identity, attribute, composition, and
        association — unlike concept_ancestors / concept_descendants which only
        follow the parent/child hierarchy.

        Use this to discover what is conceptually related to a concept without
        knowing in advance which relationship types connect them.  Typical uses:
          - finding associated anatomical sites, morphologies, or procedures
            for a clinical finding
          - mapping a drug concept to its ingredient, dose forms, and routes
          - exploring OMOP concept class membership and equivalence clusters

        predicate_kinds: optional list restricting which edge types to follow.
          Valid values: HIERARCHY, IDENTITY, ATTRIBUTE, COMPOSITION, ASSOCIATION.
          When omitted, all relationship types are traversed.

        max_depth: maximum hops from the seed (1-4, server-enforced).

        max_nodes: stop after visiting this many distinct concepts (10-500,
          server-enforced).  terminated_early=true and terminated_reason="max_nodes"
          in the response indicate the traversal was cut short.

        include_edges: when true (default) the edges list contains every
          relationship edge in the discovered subgraph with its predicate_kind.
          Set false when you only need the neighbor concept list.
        """
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        safe_depth = max(1, min(max_depth, 4))
        safe_nodes = max(10, min(max_nodes, 500))
        try:
            return graph_service.get_neighbors(
                concept_id=concept_id,
                max_depth=safe_depth,
                predicate_kinds=predicate_kinds,
                max_nodes=safe_nodes,
                include_edges=include_edges,
            )
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_associations")
    def concept_associations(
        concept_id: int,
        direction: str = "out",
        predicate_subkinds: list[str] | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Enumerate a concept's ASSOCIATION-kind relationships (clinical/therapeutic links).

        Returns the edges classified as ASSOCIATION — therapeutic and associative
        links such as a HemOnc regimen to its component drugs ("Has cytotoxic chemo",
        "Has cytotox chemo Rx"), drug<->indication, or antineoplastic relationships.
        Each edge carries the related concept and its standard_concept flag, so a
        regimen resolves straight to its standard RxNorm ingredients in one call.

        Why this is a separate tool: concept_neighbors and concept_relationships do
        NOT surface ASSOCIATION edges (the graph's edge view omits them), and
        concept_path only reaches them between two already-known concepts. Use this
        to *enumerate* what a concept is clinically associated with.

        direction: "out" (default; edges where this concept is the subject), "in", or "both".
        predicate_subkinds: optional filter on the classification subkind, e.g. ["Therapeutic"].
        active_only: when true (default) exclude invalid/deprecated relationships.
        limit: max edges returned per direction (1-200, server-enforced).
        """
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        if direction not in ("out", "in", "both"):
            return {"error": True, "code": "INVALID_INPUT", "message": "direction must be 'out', 'in', or 'both'"}
        safe_limit = max(1, min(limit, 200))
        try:
            return graph_service.get_associations(
                concept_id,
                direction=direction,
                predicate_subkinds=predicate_subkinds,
                active_only=active_only,
                limit=safe_limit,
            )
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_extended_inheritance")
    def concept_extended_inheritance(
        concept_id: int,
        direction: str = "out",
        predicate_subkinds: list[str] | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Enumerate a concept's HIERARCHY-kind relationship edges — the EXTENDED
        inheritance view. This is deliberately NOT the same as concept_ancestors /
        concept_descendants; do not use it as a substitute for strict ancestry.

        The difference matters (for people and agents):
          - concept_ancestors / concept_descendants walk the OMOP `concept_ancestor`
            table — the transitive closure of standard "Is a" ancestry between
            STANDARD concepts. That is the canonical "what is this a kind of / what
            are the kinds of this" question.
          - concept_extended_inheritance returns the raw HIERARCHY-classified edges
            straight from concept_relationship. That includes BOTH Taxonomic
            (Is a / Subsumes) AND Categorical (classification, e.g. ATC) subkinds,
            single-hop (non-transitive) edges, cross-vocabulary links, and edges
            touching non-standard concepts — none of which the ancestor closure
            exposes.

        Rule of thumb: reach for concept_ancestors/concept_descendants first. Use
        this tool only when you specifically need the broader edge-level inheritance
        graph (e.g. classification memberships, cross-vocabulary hierarchy links).

        direction: "out" (default; edges where this concept is the subject), "in", or "both".
        predicate_subkinds: optional filter, e.g. ["Taxonomic - up"], ["Categorical - up"].
        active_only: when true (default) exclude invalid/deprecated relationships.
        limit: max edges returned per direction (1-200, server-enforced).
        """
        if concept_id <= 0:
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
        if direction not in ("out", "in", "both"):
            return {"error": True, "code": "INVALID_INPUT", "message": "direction must be 'out', 'in', or 'both'"}
        safe_limit = max(1, min(limit, 200))
        try:
            return graph_service.get_extended_inheritance(
                concept_id,
                direction=direction,
                predicate_subkinds=predicate_subkinds,
                active_only=active_only,
                limit=safe_limit,
            )
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_map_to_standard")
    def concept_map_to_standard(vocabulary_id: str, concept_code: str) -> dict[str, Any]:
        """Maps a vocabulary concept code to its standard OMOP equivalent(s)."""
        if not vocabulary_id.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "vocabulary_id must be a non-empty string"}
        if not concept_code.strip():
            return {"error": True, "code": "INVALID_INPUT", "message": "concept_code must be a non-empty string"}
        try:
            return graph_service.map_to_standard(vocabulary_id, concept_code)
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}
