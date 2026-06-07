from __future__ import annotations

from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services import MappingService


def register_mapping_tools(server: GroundcrewServer, mapping_service: MappingService | None) -> None:
    @server.tool("concept_search_normalized")
    def concept_search_normalized(
        query: str,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        include_synonyms: bool = False,
        normalization_profile: str = "verbatim",
        remove_stop_phrases: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        if mapping_service is None:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": "mapping service is not configured"}
        safe_limit = max(1, min(limit, 50))
        try:
            return mapping_service.concept_search_normalized(
                query,
                domain=domain,
                vocabulary_id=vocabulary_id,
                standard_only=standard_only,
                include_synonyms=include_synonyms,
                normalization_profile=normalization_profile,
                remove_stop_phrases=remove_stop_phrases,
                limit=safe_limit,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_candidate_bundle")
    def concept_candidate_bundle(
        query: str,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        active_only: bool = True,
        include_synonyms: bool = True,
        include_normalized: bool = True,
        include_fulltext: bool = True,
        include_embedding: bool = True,
        include_standard_mappings: bool = True,
        include_hierarchy_context: bool = False,
        include_relationship_summary: bool = False,
        parent_ids: list[int] | None = None,
        per_channel_limit: int = 10,
        overall_limit: int = 30,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if mapping_service is None:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": "mapping service is not configured"}
        channel_limit = max(1, min(per_channel_limit, 20))
        safe_union_limit = max(1, min(overall_limit, 100))
        try:
            return mapping_service.concept_candidate_bundle(
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
                per_channel_limit=channel_limit,
                overall_limit=safe_union_limit,
                model_name=model_name,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_nearest_standard_ancestor")
    def concept_nearest_standard_ancestor(
        query: str | None = None,
        concept_id: int | None = None,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        parent_ids: list[int] | None = None,
        max_depth: int = 5,
        candidate_limit: int = 10,
    ) -> dict[str, Any]:
        if mapping_service is None:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": "mapping service is not configured"}
        safe_depth = max(1, min(max_depth, 10))
        safe_limit = max(1, min(candidate_limit, 20))
        try:
            return mapping_service.concept_nearest_standard_ancestor(
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
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_mapping_context")
    def concept_mapping_context(
        concept_id: int,
        include_standard_mapping: bool = True,
        include_ancestors: bool = True,
        include_descendants: bool = False,
        include_relationship_summary: bool = True,
        include_neighbors: bool = True,
        include_embedding_neighbors: bool = False,
        ancestor_limit: int = 5,
        descendant_limit: int = 10,
        neighbor_limit: int = 15,
        embedding_neighbor_limit: int = 10,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        if mapping_service is None:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": "mapping service is not configured"}
        try:
            return mapping_service.concept_mapping_context(
                concept_id,
                include_standard_mapping=include_standard_mapping,
                include_ancestors=include_ancestors,
                include_descendants=include_descendants,
                include_relationship_summary=include_relationship_summary,
                include_neighbors=include_neighbors,
                include_embedding_neighbors=include_embedding_neighbors,
                ancestor_limit=max(1, min(ancestor_limit, 10)),
                descendant_limit=max(1, min(descendant_limit, 10)),
                neighbor_limit=max(1, min(neighbor_limit, 100)),
                embedding_neighbor_limit=max(1, min(embedding_neighbor_limit, 20)),
                model_name=model_name,
            )
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_map_to_value")
    def concept_map_to_value(vocabulary_id: str, concept_code: str) -> dict[str, Any]:
        if mapping_service is None:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": "mapping service is not configured"}
        try:
            return mapping_service.concept_map_to_value(vocabulary_id, concept_code)
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("concept_resolve_mapping_expression")
    def concept_resolve_mapping_expression(
        items: list[dict[str, Any]],
        domain: str | None = None,
        deduplicate: bool = True,
        resolve_to_standard: bool = True,
    ) -> dict[str, Any]:
        if mapping_service is None:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": "mapping service is not configured"}
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
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("mapping_evaluate_candidates")
    def mapping_evaluate_candidates(
        predicted_mappings: list[dict[str, Any]],
        reference_mappings: list[dict[str, Any]],
        match_mode: str = "standard_concept_id",
        top_k: int | None = None,
        group_by_domain: bool = True,
    ) -> dict[str, Any]:
        if mapping_service is None:
            return {"error": True, "code": "BACKEND_UNAVAIL", "message": "mapping service is not configured"}
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
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}
