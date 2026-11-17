from __future__ import annotations

from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from groundworkers.base.ancestry import (
    AncestorDepth,
    AncestorLimit,
    AncestorOffset,
    ConceptIds,
    ConstraintIds,
    SharedAncestorResponse,
    validate_ancestor_depth,
)
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.base.sql import NormalizationProfile
from groundworkers.services import MappingService
from groundworkers.toolsets import Toolset


def register_mapping_tools(
    server: GroundworkersMCPServer, mapping_service: MappingService
) -> None:
    @server.tool("concept_search_normalized", toolsets=(Toolset.SEARCH,))
    def concept_search_normalized(
        query: Annotated[str, Field(description="Text to match after normalization.")],
        domain: Annotated[
            str | None, Field(description="OMOP domain_id filter.")
        ] = None,
        vocabulary_id: Annotated[
            str | None, Field(description="Exact vocabulary_id filter.")
        ] = None,
        parent_ids: Annotated[
            list[int] | None,
            Field(description="Keep only descendants of these concept_ids."),
        ] = None,
        standard_only: Annotated[
            bool, Field(description="Only standard concepts.")
        ] = False,
        include_synonyms: Annotated[
            bool, Field(description="Also match synonyms.")
        ] = False,
        normalization_profile: Annotated[
            NormalizationProfile,
            Field(description="Normalization profile; default verbatim."),
        ] = NormalizationProfile.VERBATIM,
        remove_stop_phrases: Annotated[
            bool, Field(description="Strip common stop phrases before matching.")
        ] = True,
        limit: Annotated[int, Field(description="Maximum results.")] = 20,
        allow_unindexed_scan: Annotated[
            bool,
            Field(
                description="Allow a slow unscoped scan when no normalized index exists."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Near-verbatim search after normalizing punctuation and whitespace; deterministic,
        unranked. concept_candidate_bundle already runs this channel; call it directly only for
        a strict near-exact check."""
        # Without a normalized index, use a domain, vocabulary or parent scope for a
        # time-limited scan; an unscoped scan requires explicit opt-in.
        safe_limit = max(1, min(limit, 50))
        try:
            return mapping_service.concept_search_normalized(
                query,
                domain=domain,
                vocabulary_id=vocabulary_id,
                parent_ids=parent_ids,
                standard_only=standard_only,
                include_synonyms=include_synonyms,
                normalization_profile=normalization_profile,
                remove_stop_phrases=remove_stop_phrases,
                limit=safe_limit,
                allow_unindexed_scan=allow_unindexed_scan,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_candidate_bundle", toolsets=(Toolset.SEARCH, Toolset.MAPPING))
    async def concept_candidate_bundle(
        query: Annotated[str, Field(description="The source term text, as written.")],
        domain: Annotated[
            str | None,
            Field(
                description="OMOP domain_id to restrict to, for example Measurement, Drug or Condition. Use it when known."
            ),
        ] = None,
        vocabulary_id: Annotated[
            str | None, Field(description="Restrict to one exact vocabulary_id.")
        ] = None,
        standard_only: Annotated[
            bool,
            Field(
                description="true returns only standard concepts. The default false also returns non-standard hits (with their standard mapping when include_standard_mappings is true)."
            ),
        ] = False,
        active_only: Annotated[
            bool, Field(description="Exclude retired (invalid) concepts. Default true.")
        ] = True,
        include_synonyms: Annotated[
            bool, Field(description="Also match concept synonyms.")
        ] = True,
        include_normalized: Annotated[
            bool, Field(description="Run the normalized channel.")
        ] = True,
        include_fulltext: Annotated[
            bool, Field(description="Run the full-text channel.")
        ] = True,
        include_embedding: Annotated[
            bool, Field(description="Run the embedding channel.")
        ] = True,
        include_standard_mappings: Annotated[
            bool,
            Field(description="Attach each candidate's Maps-to standard concept(s)."),
        ] = True,
        include_hierarchy_context: Annotated[
            bool,
            Field(
                description="Attach ancestors to the first five candidates (larger output)."
            ),
        ] = False,
        include_relationship_summary: Annotated[
            bool,
            Field(
                description="Attach a relationship summary to the first five candidates (larger output)."
            ),
        ] = False,
        parent_ids: Annotated[
            list[int] | None,
            Field(
                description="Keep only descendants of these concept_ids. Applies to the exact, normalized and full-text channels, not embedding."
            ),
        ] = None,
        per_channel_limit: Annotated[
            int, Field(description="Maximum results per channel.")
        ] = 10,
        overall_limit: Annotated[
            int, Field(description="Maximum candidates in the merged list.")
        ] = 30,
        model_name: Annotated[
            str | None,
            Field(description="Embedding model; omit to use the configured default."),
        ] = None,
    ) -> dict[str, Any]:
        """Default first search for a source term. Runs exact, normalized, full-text and embedding
        search together and returns one deduplicated candidate list; retrieved_by shows which
        channels found each candidate. Pass domain when known. Candidates are leads: check
        standard_concept and domain_id, then the hierarchy (concept_mapping_context,
        concept_descendants) before choosing."""
        # Keep channel results in the service response for in-process and REST callers.
        # Only the MCP boundary below replaces them with availability and counts.
        try:
            result = await mapping_service.async_concept_candidate_bundle(
                query,
                domain=domain,
                vocabulary_id=vocabulary_id,
                standard_only=standard_only,
                active_only=active_only,
                include_synonyms=include_synonyms,
                include_normalized=include_normalized,
                include_fulltext=include_fulltext,
                include_embedding=include_embedding,
                include_standard_mappings=include_standard_mappings,
                include_hierarchy_context=include_hierarchy_context,
                include_relationship_summary=include_relationship_summary,
                parent_ids=parent_ids,
                per_channel_limit=per_channel_limit,
                overall_limit=overall_limit,
                model_name=model_name,
            )
            return {
                **result,
                "channels": {
                    name: {
                        "available": channel["available"],
                        "count": len(channel["results"]),
                    }
                    for name, channel in result["channels"].items()
                },
            }
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool(
        "concept_shared_standard_ancestor",
        toolsets=(Toolset.MAPPING_ADVANCED,),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        result_model=SharedAncestorResponse,
    )
    def concept_shared_standard_ancestor(
        concept_ids: ConceptIds,
        max_depth: AncestorDepth = 5,
        concept_class_ids: ConstraintIds | None = None,
        vocabulary_ids: ConstraintIds | None = None,
        limit: AncestorLimit = 10,
        offset: AncestorOffset = 0,
    ) -> dict[str, Any]:
        """Find common active standard ancestors of alternative candidates for one source.

        Supply 2..200 positive concept IDs in the same domain (at least two distinct).
        Class/vocabulary filters apply before selection; null means unrestricted;
        empty or unknown filters are invalid. Exact vocabulary IDs are required:
        RxNorm does not include RxNorm Extension. Use system_vocabulary_catalogue
        to discover available identifiers.

        max_depth=0..20 measures stored minimum separation, not parent hops.
        Distinct zero-distance ancestors count, and each seed counts as itself.
        Returns selected, ambiguous, no_match or incompatible_inputs. found means
        a unique selection, not simply the existence of common ancestry.
        limit/offset page the complete common set and never change selection.

        A shared ancestor is a generalization, not proof of equivalence or clinical
        correctness. Do not merge separate combination-drug ingredients with this
        operation. Deterministic for a fixed vocabulary; no LLM/embeddings required.
        """
        result = mapping_service.concept_shared_standard_ancestor(
            concept_ids,
            max_depth=max_depth,
            concept_class_ids=concept_class_ids,
            vocabulary_ids=vocabulary_ids,
            limit=limit,
            offset=offset,
        )
        # Registration advertises and validates the typed envelope after guarding.
        return {"data": result}

    @server.tool("concept_nearest_standard_ancestor", toolsets=(Toolset.MAPPING,))
    async def concept_nearest_standard_ancestor(
        query: Annotated[
            str | None,
            Field(description="Free text to ground first (use this or concept_id)."),
        ] = None,
        concept_id: Annotated[
            int | None, Field(description="Start concept (use this or query).")
        ] = None,
        domain: Annotated[
            str | None, Field(description="OMOP domain_id filter.")
        ] = None,
        vocabulary_id: Annotated[
            str | None,
            Field(
                description="Restrict grounded seeds in query mode to one exact vocabulary_id."
            ),
        ] = None,
        parent_ids: Annotated[
            list[int] | None,
            Field(
                description="Keep only grounded seeds in query mode that are descendants of these concept_ids."
            ),
        ] = None,
        max_depth: Annotated[
            AncestorDepth, Field(description="Maximum stored separation, 0-20.")
        ] = 5,
        candidate_limit: Annotated[
            int, Field(description="Maximum grounded seeds retrieved in query mode.")
        ] = 10,
    ) -> dict[str, Any]:
        """When no standard concept matches the source at its precision, find the closest broader
        standard concept, from a concept_id or a free-text query. Equally near candidates are
        reported as ambiguous."""
        # Stored separation includes zero-distance ancestry. Query mode uses the
        # first ranked seed; Maps-to fallback is reported separately from ancestry.
        safe_depth = validate_ancestor_depth(max_depth)
        safe_limit = max(1, min(candidate_limit, 20))
        try:
            return await mapping_service.async_concept_nearest_standard_ancestor(
                query=query,
                concept_id=concept_id,
                domain=domain,
                vocabulary_id=vocabulary_id,
                parent_ids=parent_ids,
                max_depth=safe_depth,
                candidate_limit=safe_limit,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_mapping_context", toolsets=(Toolset.MAPPING,))
    def concept_mapping_context(
        concept_id: Annotated[int, Field(description="The candidate to review.")],
        include_standard_mapping: Annotated[
            bool, Field(description="Include its Maps-to target.")
        ] = True,
        include_ancestors: Annotated[
            bool, Field(description="Include broader concepts.")
        ] = True,
        include_descendants: Annotated[
            bool, Field(description="Include more specific concepts (default false).")
        ] = False,
        include_relationship_summary: Annotated[
            bool, Field(description="Include counts of relationship types.")
        ] = True,
        include_neighbors: Annotated[
            bool, Field(description="Include graph neighbours.")
        ] = True,
        include_embedding_neighbors: Annotated[
            bool, Field(description="Include nearest concepts by embedding.")
        ] = False,
        ancestor_limit: Annotated[
            AncestorLimit, Field(description="Maximum ancestors.")
        ] = 5,
        ancestor_max_depth: Annotated[
            AncestorDepth, Field(description="Maximum ancestor separation.")
        ] = 5,
        descendant_limit: Annotated[
            int, Field(description="Maximum descendants.")
        ] = 10,
        neighbor_limit: Annotated[int, Field(description="Maximum neighbours.")] = 15,
        embedding_neighbor_limit: Annotated[
            int, Field(description="Maximum embedding neighbours.")
        ] = 10,
        model_name: Annotated[
            str | None,
            Field(description="Embedding model; omit to use the configured default."),
        ] = None,
    ) -> dict[str, Any]:
        """One-call review of a candidate before committing to it: standard mapping, ancestors,
        relationship summary and graph neighbours. Set include_descendants=true to also list
        more specific concepts."""
        try:
            return mapping_service.concept_mapping_context(
                concept_id,
                include_standard_mapping=include_standard_mapping,
                include_ancestors=include_ancestors,
                include_descendants=include_descendants,
                include_relationship_summary=include_relationship_summary,
                include_neighbors=include_neighbors,
                include_embedding_neighbors=include_embedding_neighbors,
                ancestor_limit=ancestor_limit,
                ancestor_max_depth=ancestor_max_depth,
                descendant_limit=max(1, min(descendant_limit, 10)),
                neighbor_limit=max(1, min(neighbor_limit, 100)),
                embedding_neighbor_limit=max(1, min(embedding_neighbor_limit, 20)),
                model_name=model_name,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("concept_map_to_value", toolsets=(Toolset.MAPPING_ADVANCED,))
    def concept_map_to_value(vocabulary_id: str, concept_code: str) -> dict[str, Any]:
        """Return "Maps to value" target concepts for a given vocabulary/code pair."""
        try:
            return mapping_service.concept_map_to_value(vocabulary_id, concept_code)
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool(
        "concept_resolve_mapping_expression", toolsets=(Toolset.MAPPING_ADVANCED,)
    )
    def concept_resolve_mapping_expression(
        items: list[dict[str, Any]],
        domain: str | None = None,
        deduplicate: bool = True,
        resolve_to_standard: bool = True,
    ) -> dict[str, Any]:
        """Resolve a structured mapping expression to standard OMOP concepts.

        Accepts a list of items each specifying a vocabulary_id and concept_code (or
        concept_id). Resolves each to its standard equivalent(s) and optionally
        deduplicates the combined result set. Useful for batch-resolving a pre-defined
        concept set from source vocabulary codes.
        """
        try:
            return mapping_service.concept_resolve_mapping_expression(
                items,
                domain=domain,
                deduplicate=deduplicate,
                resolve_to_standard=resolve_to_standard,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("mapping_evaluate_candidates", toolsets=(Toolset.OFFLINE,))
    def mapping_evaluate_candidates(
        predicted_mappings: Annotated[
            list[dict[str, Any]],
            Field(
                description="Rows with source_key (or source_id or source_term), predicted_standard_concept_ids as a ranked list, optional domain_id."
            ),
        ],
        reference_mappings: Annotated[
            list[dict[str, Any]],
            Field(
                description="Rows with source_key (or source_id or source_term), reference_standard_concept_id (required), optional domain_id."
            ),
        ],
        match_mode: str = "standard_concept_id",
        top_k: Annotated[
            int | None,
            Field(
                description="Count a match within the first top_k predictions; omit to use all."
            ),
        ] = None,
        group_by_domain: bool = True,
    ) -> dict[str, Any]:
        """Score predicted mappings against reference mappings: accuracy (the reference standard
        concept_id is among the first top_k predictions), coverage and agreement cases,
        optionally by domain. An evaluation utility; concept_ids are not checked against the
        vocabulary."""
        try:
            return mapping_service.mapping_evaluate_candidates(
                predicted_mappings,
                reference_mappings,
                match_mode=match_mode,
                top_k=top_k,
                group_by_domain=group_by_domain,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
