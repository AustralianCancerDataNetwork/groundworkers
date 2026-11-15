from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from groundworkers.base.ancestry import (
    AncestorDepth,
    PositiveConceptId,
    validate_ancestor_depth,
)
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.graph import GraphService
from groundworkers.toolsets import Toolset


def register_concept_tools(
    server: GroundworkersMCPServer, graph_service: GraphService
) -> None:
    """Register deterministic concept lookup tools against the MCP server.

    These tools take a known identifier (concept_id, vocabulary+code) and return
    a fact — they are deterministic lookups, not text matching.

    For free-text grounding see resolver_tools.py.
    For direct lexical search with retrieval signals see search_tools.py.
    """

    @server.tool("concept_get", toolsets=(Toolset.LOOKUP,))
    def concept_get(
        concept_id: Annotated[int, Field(description="OMOP concept_id.")],
    ) -> dict[str, Any]:
        """Look up one concept by concept_id: name, vocabulary, domain, class and standard flag.
        Use it to verify a candidate before answering. To find concepts from text, use
        concept_candidate_bundle."""
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
        try:
            concept = graph_service.get_concept(concept_id)
            if concept is None:
                return {
                    "error": True,
                    "code": "NOT_FOUND",
                    "message": f"Concept {concept_id} was not found",
                }
            return concept
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_by_code", toolsets=(Toolset.LOOKUP,))
    def concept_by_code(
        vocabulary_id: Annotated[
            str, Field(description="Exact vocabulary_id, for example ICD10CM or LOINC.")
        ],
        concept_code: Annotated[
            str, Field(description="The code as it appears in that vocabulary.")
        ],
    ) -> dict[str, Any]:
        """Look up one concept by vocabulary_id and concept_code. Use it for codes that appear in
        the source data or in tool results. Codes recalled from memory are often wrong; to find
        a concept by its meaning, search with concept_candidate_bundle."""
        if not vocabulary_id.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "vocabulary_id must be a non-empty string",
            }
        if not concept_code.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_code must be a non-empty string",
            }
        try:
            concepts = graph_service.get_concept_by_code(vocabulary_id, concept_code)
            if not concepts:
                return {
                    "error": True,
                    "code": "NOT_FOUND",
                    "message": f"Concept {vocabulary_id}:{concept_code} was not found",
                    "hint": (
                        "No concept has that code in that vocabulary. If the code was recalled "
                        "from memory rather than seen in the source or a tool result, search "
                        "by meaning with concept_candidate_bundle instead."
                    ),
                }
            return {"concepts": concepts}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_ancestors", toolsets=(Toolset.GRAPH, Toolset.MAPPING))
    def concept_ancestors(
        concept_id: Annotated[
            PositiveConceptId, Field(description="The more specific concept.")
        ],
        max_depth: Annotated[
            AncestorDepth,
            Field(description="Maximum stored separation, 0-20. Default 5."),
        ] = 5,
    ) -> dict[str, Any]:
        """List broader concepts above a concept (stored OMOP hierarchy). Use it to check whether a
        candidate is more specific than the source supports, or to find a generalisation when no
        exact match exists. max_depth bounds stored separation, not hops; zero separation does
        not mean the concepts are interchangeable."""
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
        safe_depth = validate_ancestor_depth(max_depth)
        try:
            ancestors = graph_service.get_ancestors(concept_id, safe_depth)
            return {
                "concept_id": concept_id,
                "ancestors": ancestors,
                "ancestry_source": "concept_ancestor",
                "distance_metric": "min_levels_of_separation",
                "max_depth": safe_depth,
                "include_self": False,
                "selection_complete": True,
            }
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_descendants", toolsets=(Toolset.GRAPH, Toolset.MAPPING))
    def concept_descendants(
        concept_id: Annotated[int, Field(description="The broader concept.")],
        max_depth: Annotated[
            int,
            Field(description="Levels below to include (hierarchy hops). Default 3."),
        ] = 3,
    ) -> dict[str, Any]:
        """List more specific concepts below a concept (stored OMOP hierarchy, up to max_depth
        levels). Use it after picking a candidate, to check whether a more specific standard
        concept matches every qualifier the source states (for example specimen, site, route or
        form). Each result has domain_id, standard_concept and depth."""
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
        safe_depth = max(1, min(max_depth, 10))
        try:
            descendants = graph_service.get_descendants(concept_id, safe_depth)
            return {"concept_id": concept_id, "descendants": descendants}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_relationships", toolsets=(Toolset.GRAPH,))
    def concept_relationships(concept_id: int) -> dict[str, Any]:
        """Returns all relationships for a concept grouped by direction (inbound/outbound).

        Edges are not filtered by domain, so cross-domain attribute edges are
        included: e.g. a Procedure's "Has method" (-> Observation), "Has dir
        proc site" (-> Spec Anatomic Site) and "Has dir device" (-> Device).
        """
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
        try:
            edges = graph_service.get_edges(concept_id)
            return {"concept_id": concept_id, **edges}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_equivalency_path", toolsets=(Toolset.GRAPH,))
    def concept_equivalency_path(
        source_id: int,
        target_id: int,
        allow_hierarchical_traversal: bool = False,
        max_depth: int = 8,
    ) -> dict[str, Any]:
        """Shortest equivalence path between two concepts over identity edges (Maps to, same_as,
        cross-vocabulary equivalence and replacement links). With
        allow_hierarchical_traversal=true it may also step up or down the hierarchy, so the link
        found can be broader than an exact equivalence."""
        # Identity traversal always allows cross-domain edges and excludes attribute,
        # composition and association edges. Hierarchy traversal is optional.
        if source_id <= 0 or target_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "source_id and target_id must be positive integers",
            }
        safe_depth = max(2, min(max_depth, 15))
        try:
            result = graph_service.find_equivalency_path(
                source_id, target_id, safe_depth, allow_hierarchical_traversal
            )
            return {"source_id": source_id, "target_id": target_id, **result}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_path", toolsets=(Toolset.GRAPH,))
    def concept_path(
        source_id: int,
        target_id: int,
        max_depth: int = 8,
        within_domain: bool = True,
    ) -> dict[str, Any]:
        """Shortest path between two known concepts over any relationship type. within_domain=true
        (default) keeps both ends of every edge in one domain; set it false to cross domains
        through attribute edges such as finding site."""
        if source_id <= 0 or target_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "source_id and target_id must be positive integers",
            }
        safe_depth = max(2, min(max_depth, 15))
        try:
            result = graph_service.find_path(
                source_id, target_id, safe_depth, within_domain=within_domain
            )
            return {"source_id": source_id, "target_id": target_id, **result}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_neighbors", toolsets=(Toolset.GRAPH,))
    def concept_neighbors(
        concept_id: int,
        max_depth: int = 2,
        predicate_kinds: list[str] | None = None,
        max_nodes: int = 100,
        include_edges: bool = True,
    ) -> dict[str, Any]:
        """Breadth-first exploration of everything linked to a concept across all relationship
        types (hierarchy, identity, attribute, composition, association), up to 4 hops. Use it
        to discover related concepts when you do not know which relationship connects them, for
        example a procedure's site and method (predicate_kinds=["ATTRIBUTE"], max_depth=1)."""
        # Follow outgoing edges across domains. predicate_kinds accepts HIERARCHY,
        # IDENTITY, ATTRIBUTE, COMPOSITION and ASSOCIATION. max_nodes is clamped to
        # 10..500; terminated_early and terminated_reason disclose truncation.
        # include_edges retains discovered-subgraph edges and their predicate_kind.
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
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

    @server.tool("concept_associations", toolsets=(Toolset.GRAPH,))
    def concept_associations(
        concept_id: int,
        direction: str = "out",
        predicate_subkinds: list[str] | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List a concept's clinical or therapeutic association edges (for example regimen to
        component drugs, drug to indication), each with the related concept's standard flag.
        Defining attributes such as finding site are ATTRIBUTE edges; use concept_neighbors for
        those."""
        # direction selects outgoing, incoming or both directions. predicate_subkinds
        # narrows classification subkinds; active_only excludes invalid relationships.
        # The edge limit is clamped to 1..200 per direction.
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
        if direction not in ("out", "in", "both"):
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "direction must be 'out', 'in', or 'both'",
            }
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

    @server.tool("concept_extended_inheritance", toolsets=(Toolset.GRAPH,))
    def concept_extended_inheritance(
        concept_id: int,
        direction: str = "out",
        predicate_subkinds: list[str] | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Raw single-hop hierarchy edges from concept_relationship, including classification (for
        example ATC) and cross-vocabulary links. Use concept_ancestors or concept_descendants
        for ordinary is-a questions; use this only for classification membership or
        cross-vocabulary hierarchy edges."""
        # Raw hierarchy edges include taxonomic and categorical subkinds, non-standard
        # endpoints and cross-vocabulary links. direction, predicate_subkinds and
        # active_only filter edges; limit is clamped to 1..200 per direction.
        if concept_id <= 0:
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_id must be a positive integer",
            }
        if direction not in ("out", "in", "both"):
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "direction must be 'out', 'in', or 'both'",
            }
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

    @server.tool("concept_map_to_standard", toolsets=(Toolset.MAPPING_ADVANCED,))
    def concept_map_to_standard(
        vocabulary_id: Annotated[
            str, Field(description="Exact vocabulary_id of the code.")
        ],
        concept_code: Annotated[str, Field(description="The source code.")],
    ) -> dict[str, Any]:
        """Resolve a vocabulary_id and concept_code (for example a source code) to its standard
        concept(s) via identity relationships, which include Maps to value; for the Maps to
        target alone, use concept_navigate_to_standard."""
        if not vocabulary_id.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "vocabulary_id must be a non-empty string",
            }
        if not concept_code.strip():
            return {
                "error": True,
                "code": "INVALID_INPUT",
                "message": "concept_code must be a non-empty string",
            }
        try:
            return graph_service.map_to_standard(vocabulary_id, concept_code)
        except GroundworkersError as exc:
            return exc.to_dict()
