from __future__ import annotations

from collections import Counter
from typing import Any

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.services.vocab import (
    VocabService,
    normalize_text_for_matching,
    serialise_concept_match,
    serialise_related_concept_mapping,
    serialise_standard_mapping,
)


class MappingService:
    """Direct Python API for mapping-oriented vocabulary workflows."""

    def __init__(
        self,
        vocab: VocabService,
        *,
        graph_adapter: OmopGraphAdapter | None = None,
        emb_adapter: OmopEmbAdapter | None = None,
    ) -> None:
        self._vocab = vocab
        self._graph = graph_adapter
        self._emb = emb_adapter

    def concept_search_normalized(
        self,
        query: str,
        *,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        standard_only: bool = False,
        include_synonyms: bool = False,
        normalization_profile: str = "verbatim",
        remove_stop_phrases: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        stripped = query.strip()
        if not stripped:
            raise ValueError("query must be a non-empty string")
        normalized_query, normalization_steps = normalize_text_for_matching(
            query,
            profile=normalization_profile,
            remove_stop_phrases=remove_stop_phrases,
        )
        if not normalized_query:
            raise ValueError("query must contain searchable content after normalization")

        results = self._vocab.search_normalized(
            query,
            domain=domain or None,
            vocabulary_id=vocabulary_id or None,
            standard_only=standard_only,
            include_synonyms=include_synonyms,
            normalization_profile=normalization_profile,
            remove_stop_phrases=remove_stop_phrases,
            limit=limit,
        )
        serialised = []
        for match in results:
            row = serialise_concept_match(match)
            matched_text = match.matched_synonym or match.concept_name
            matched_text_normalized, _ = normalize_text_for_matching(
                matched_text,
                profile=normalization_profile,
                remove_stop_phrases=remove_stop_phrases,
            )
            row["matched_text"] = matched_text
            row["matched_text_normalized"] = matched_text_normalized
            row["match_mode"] = (
                "synonym_exact_normalized" if match.match_source == "synonym" else "label_exact_normalized"
            )
            serialised.append(row)
        return {
            "query": stripped,
            "normalized_query": normalized_query,
            "normalization_profile": normalization_profile,
            "normalization_steps": normalization_steps,
            "results": serialised,
        }

    def concept_candidate_bundle(
        self,
        query: str,
        *,
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
        stripped = query.strip()
        if not stripped:
            raise ValueError("query must be a non-empty string")
        if parent_ids is not None and any(pid <= 0 for pid in parent_ids):
            raise ValueError("all parent_ids must be positive integers")

        warnings: list[str] = []
        channels: dict[str, dict[str, Any]] = {}

        exact_results = self._vocab.search_exact(
            query,
            domain=domain or None,
            vocabulary_id=vocabulary_id or None,
            standard_only=standard_only,
            active_only=active_only,
            include_synonyms=include_synonyms,
            parent_ids=parent_ids,
            limit=per_channel_limit,
        )
        channels["exact"] = {
            "available": True,
            "results": [serialise_concept_match(r) for r in exact_results],
            "retrieval_notes": ["case-insensitive exact match over concept_name and optional synonyms"],
        }

        if include_normalized:
            normalized_results = self._vocab.search_normalized(
                query,
                domain=domain or None,
                vocabulary_id=vocabulary_id or None,
                standard_only=standard_only,
                active_only=active_only,
                include_synonyms=False,
                parent_ids=parent_ids,
                limit=per_channel_limit,
            )
            channels["normalized"] = {
                "available": True,
                "results": [serialise_concept_match(r) for r in normalized_results],
                "retrieval_notes": ["deterministic normalized equality over concept labels"],
            }

        if include_fulltext:
            fts_results, fts_available = self._vocab.search_fulltext(
                query,
                domain=domain or None,
                vocabulary_id=vocabulary_id or None,
                standard_only=standard_only,
                active_only=active_only,
                include_synonyms=include_synonyms,
                parent_ids=parent_ids,
                limit=per_channel_limit,
            )
            channels["fulltext"] = {
                "available": fts_available,
                "results": [serialise_concept_match(r) for r in fts_results],
                "retrieval_notes": ["ranked PostgreSQL full-text retrieval"] if fts_available else [],
            }
            if not fts_available:
                warnings.append("full-text sidecar columns unavailable; fulltext channel omitted")

        if include_embedding:
            if self._emb is None:
                channels["embedding"] = {"available": False, "results": [], "retrieval_notes": []}
                warnings.append("embedding adapter not configured; embedding channel omitted")
            else:
                try:
                    embedding_result = self._emb.search(
                        query=query,
                        limit=per_channel_limit,
                        domain=domain,
                        vocabulary=vocabulary_id,
                        standard_only=standard_only,
                        active_only=active_only,
                        model_name=model_name,
                    )
                    emb_notes = ["semantic retrieval from omop-emb"]
                    if parent_ids:
                        emb_notes.append("parent_ids hierarchy filter was not applied at the embedding level")
                    channels["embedding"] = {
                        "available": True,
                        "results": embedding_result.get("results", []),
                        "retrieval_notes": emb_notes,
                    }
                except GroundworkersError as exc:
                    channels["embedding"] = {"available": False, "results": [], "retrieval_notes": []}
                    warnings.append(f"embedding channel unavailable: {exc.message}")

        candidate_union = self._build_candidate_union(channels, overall_limit)

        standardized_candidates: list[dict[str, Any]] = []
        if include_standard_mappings and candidate_union:
            concept_ids = [row["concept_id"] for row in candidate_union if not row.get("standard_concept")]
            if concept_ids:
                mappings = self._vocab.navigate_to_standard(concept_ids)
                standardized_candidates = [serialise_standard_mapping(m) for m in mappings]
                mapping_index = {m["source_concept_id"]: m["standard_concepts"] for m in standardized_candidates}
                for row in candidate_union:
                    row["mapped_standard_concepts"] = mapping_index.get(row["concept_id"], [])

        if include_hierarchy_context and self._graph is not None:
            for row in candidate_union[: min(5, len(candidate_union))]:
                try:
                    row["ancestor_preview"] = self._graph.get_ancestors(row["concept_id"], 2)[:3]
                except Exception:
                    row["ancestor_preview"] = []
        elif include_hierarchy_context:
            warnings.append("graph adapter not configured; hierarchy context omitted")

        if include_relationship_summary and self._graph is not None:
            for row in candidate_union[: min(5, len(candidate_union))]:
                try:
                    edges = self._graph.get_edges(row["concept_id"])
                    row["relationship_summary"] = self._summarise_edges(edges)
                except Exception:
                    row["relationship_summary"] = {}
        elif include_relationship_summary:
            warnings.append("graph adapter not configured; relationship summary omitted")

        return {
            "query": stripped,
            "constraints": {
                "domain": domain,
                "vocabulary_id": vocabulary_id,
                "standard_only": standard_only,
                "active_only": active_only,
                "parent_ids": parent_ids,
            },
            "channels": channels,
            "standardized_candidates": standardized_candidates,
            "candidate_union": candidate_union,
            "warnings": warnings,
        }

    def concept_nearest_standard_ancestor(
        self,
        *,
        query: str | None = None,
        concept_id: int | None = None,
        domain: str | None = None,
        vocabulary_id: str | None = None,
        parent_ids: list[int] | None = None,
        max_depth: int = 5,
        candidate_limit: int = 10,
    ) -> dict[str, Any]:
        if self._graph is None:
            raise GroundworkersError("BACKEND_UNAVAIL", "omop_graph adapter is not configured")
        if (query is None) == (concept_id is None):
            raise ValueError("exactly one of query or concept_id must be provided")

        if query is not None:
            grounded = self._graph.ground(
                query.strip(),
                candidate_limit,
                domain or None,
                vocabulary_id or None,
                parent_ids=tuple(parent_ids) if parent_ids else None,
            )
            results = grounded["results"]
            if not results:
                return {
                    "query": query.strip(),
                    "found": False,
                    "seed_candidates": [],
                    "selected_parent": None,
                    "selection_reason": "no_candidates",
                    "alternative_parents": [],
                    "warnings": [],
                }
            seed = results[0]
            seed_concept = self._graph.get_concept(seed["concept_id"])
            if seed_concept is None:
                raise GroundworkersError("NOT_FOUND", f"Concept {seed['concept_id']} was not found")
            selection_reason = "exact_standard_match" if seed.get("match_kind") == "EXACT" else "nearest_standard_ancestor"
        else:
            assert concept_id is not None
            seed_concept = self._graph.get_concept(concept_id)
            if seed_concept is None:
                raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
            seed = {"concept_id": concept_id, "concept_name": seed_concept["concept_name"], "match_kind": "DIRECT"}
            selection_reason = "direct_concept_input"

        if selection_reason == "exact_standard_match":
            selected_parent = seed_concept
            alternatives: list[dict[str, Any]] = []
        else:
            ancestors = self._graph.get_ancestors(seed_concept["concept_id"], max_depth)
            standard_ancestors = [a for a in ancestors if a.get("standard_concept")]
            selected_parent = standard_ancestors[0] if standard_ancestors else seed_concept
            alternatives = standard_ancestors[1: min(5, len(standard_ancestors))]

        path_payload = []
        if selected_parent and selected_parent["concept_id"] != seed_concept["concept_id"]:
            path_info = self._graph.find_path(
                seed_concept["concept_id"],
                selected_parent["concept_id"],
                max_depth,
                within_domain=True,
            )
            path_payload = path_info.get("paths", [])

        return {
            "query": query.strip() if query is not None else None,
            "concept_id": concept_id,
            "found": selected_parent is not None,
            "seed_candidates": [seed],
            "selected_parent": selected_parent,
            "selection_reason": selection_reason,
            "path_to_parent": path_payload,
            "alternative_parents": alternatives,
            "warnings": [],
        }

    def concept_mapping_context(
        self,
        concept_id: int,
        *,
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
        if self._graph is None:
            raise GroundworkersError("BACKEND_UNAVAIL", "omop_graph adapter is not configured")
        if concept_id <= 0:
            raise ValueError("concept_id must be a positive integer")
        concept = self._graph.get_concept(concept_id)
        if concept is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        result: dict[str, Any] = {"concept": concept}

        if include_standard_mapping:
            result["standard_mapping"] = self._graph.map_to_standard(
                concept["vocabulary_id"],
                concept["concept_code"],
            )
        if include_ancestors:
            result["ancestors"] = self._graph.get_ancestors(concept_id, max(1, min(ancestor_limit, 10)))[:ancestor_limit]
        if include_descendants:
            result["descendants"] = self._graph.get_descendants(concept_id, max(1, min(descendant_limit, 10)))[:descendant_limit]
        if include_relationship_summary:
            result["relationship_summary"] = self._summarise_edges(self._graph.get_edges(concept_id))
        if include_neighbors:
            neighbors = self._graph.get_neighbors(
                concept_id=concept_id,
                max_depth=2,
                predicate_kinds=None,
                max_nodes=max(10, min(neighbor_limit, 100)),
                include_edges=False,
            )
            result["neighbors"] = neighbors.get("neighbors", [])[:neighbor_limit]
        if include_embedding_neighbors:
            if self._emb is None:
                result["embedding_neighbors"] = []
                result.setdefault("warnings", []).append("embedding adapter not configured")
            else:
                result["embedding_neighbors"] = self._emb.get_neighbours(
                    concept_id=concept_id,
                    limit=max(1, min(embedding_neighbor_limit, 20)),
                    model_name=model_name,
                ).get("results", [])
        return result

    def concept_map_to_value(
        self,
        vocabulary_id: str,
        concept_code: str,
    ) -> dict[str, Any]:
        if self._graph is None:
            raise GroundworkersError("BACKEND_UNAVAIL", "omop_graph adapter is not configured")
        if not vocabulary_id.strip():
            raise ValueError("vocabulary_id must be a non-empty string")
        if not concept_code.strip():
            raise ValueError("concept_code must be a non-empty string")
        source_list = self._graph.get_concept_by_code(vocabulary_id, concept_code)
        if not source_list:
            raise GroundworkersError("NOT_FOUND", f"Concept {vocabulary_id}:{concept_code} was not found")
        source = source_list[0]
        mappings = self._vocab.navigate_to_value([source["concept_id"]])
        mapping = mappings[0] if mappings else None
        return {
            "source_concept": source,
            "maps_to_value": serialise_related_concept_mapping(mapping)["related_concepts"] if mapping else [],
        }

    def concept_resolve_mapping_expression(
        self,
        items: list[dict[str, Any]],
        *,
        domain: str | None = None,
        deduplicate: bool = True,
        resolve_to_standard: bool = True,
    ) -> dict[str, Any]:
        if self._graph is None:
            raise GroundworkersError("BACKEND_UNAVAIL", "omop_graph adapter is not configured")
        if not items:
            return {"expression_items": [], "resolved_concept_ids": [], "resolved_concepts": [], "excluded_concepts": [], "counts": {"resolved": 0, "excluded": 0}}
        resolved: dict[int, dict[str, Any]] = {}
        excluded: dict[int, dict[str, Any]] = {}
        try:
            for item in items:
                cid = int(item["concept_id"])
                concept = self._graph.get_concept(cid)
                if concept is None:
                    continue
                concepts_to_apply = [concept]
                if resolve_to_standard and not concept.get("standard_concept"):
                    mapped = self._graph.map_to_standard(concept["vocabulary_id"], concept["concept_code"])
                    mapped_standards = mapped.get("standard_concepts", [])
                    if mapped_standards:
                        concepts_to_apply = mapped_standards
                expanded: list[dict[str, Any]] = []
                for base in concepts_to_apply:
                    expanded.append(base)
                    if item.get("include_descendants"):
                        expanded.extend(self._graph.get_descendants(base["concept_id"], 2))
                target = excluded if item.get("exclude") else resolved
                for entry in expanded:
                    if domain and entry.get("domain_id") and str(entry["domain_id"]).lower() != domain.lower():
                        continue
                    target[int(entry["concept_id"])] = entry
            if deduplicate:
                for cid in list(excluded):
                    resolved.pop(cid, None)
            return {
                "expression_items": items,
                "resolved_concept_ids": sorted(resolved),
                "resolved_concepts": [resolved[cid] for cid in sorted(resolved)],
                "excluded_concepts": [excluded[cid] for cid in sorted(excluded)],
                "counts": {"resolved": len(resolved), "excluded": len(excluded)},
                "warnings": [],
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "each item must include a valid concept_id and optional boolean flags"
            ) from exc

    def mapping_evaluate_candidates(
        self,
        predicted_mappings: list[dict[str, Any]],
        reference_mappings: list[dict[str, Any]],
        *,
        match_mode: str = "standard_concept_id",
        top_k: int | None = None,
        group_by_domain: bool = True,
    ) -> dict[str, Any]:
        if match_mode != "standard_concept_id":
            raise ValueError(f"unsupported match_mode: {match_mode}")

        reference_index: dict[str, dict[str, Any]] = {}
        for row in reference_mappings:
            key = self._mapping_source_key(row)
            if key is not None:
                reference_index[key] = row

        agreement_cases: list[dict[str, Any]] = []
        disagreement_cases: list[dict[str, Any]] = []
        extra_prediction_cases: list[dict[str, Any]] = []
        by_domain_counts: dict[str, Counter[str]] = {}

        for pred in predicted_mappings:
            key = self._mapping_source_key(pred)
            if key is None:
                continue
            ref = reference_index.pop(key, None)
            predicted_ids = self._extract_predicted_ids(pred, top_k=top_k)
            if ref is None:
                extra_prediction_cases.append({"source_key": key, "predicted_concept_ids": predicted_ids})
                continue
            reference_id = int(ref["reference_standard_concept_id"])
            domain_name = str(ref.get("domain_id") or pred.get("domain_id") or "UNKNOWN")
            bucket = by_domain_counts.setdefault(domain_name, Counter())
            if reference_id in predicted_ids:
                agreement_cases.append(
                    {"source_key": key, "reference_standard_concept_id": reference_id, "predicted_concept_ids": predicted_ids}
                )
                bucket["agreement"] += 1
            else:
                disagreement_cases.append(
                    {
                        "source_key": key,
                        "reference_standard_concept_id": reference_id,
                        "predicted_concept_ids": predicted_ids,
                    }
                )
                bucket["disagreement"] += 1

        missing_reference_cases = [
            {
                "source_key": key,
                "reference_standard_concept_id": int(row["reference_standard_concept_id"]),
                "domain_id": row.get("domain_id"),
            }
            for key, row in reference_index.items()
        ]

        total_compared = len(agreement_cases) + len(disagreement_cases)
        summary_metrics = {
            "accuracy": round(len(agreement_cases) / total_compared, 6) if total_compared else 0.0,
            "coverage": round(total_compared / len(reference_mappings), 6) if reference_mappings else 0.0,
            "agreement_count": len(agreement_cases),
            "disagreement_count": len(disagreement_cases),
            "missing_reference_count": len(missing_reference_cases),
            "extra_prediction_count": len(extra_prediction_cases),
        }

        by_domain = {}
        if group_by_domain:
            by_domain = {
                domain_name: {
                    "agreement": counts["agreement"],
                    "disagreement": counts["disagreement"],
                }
                for domain_name, counts in sorted(by_domain_counts.items())
            }

        return {
            "summary_metrics": summary_metrics,
            "by_domain": by_domain,
            "agreement_cases": agreement_cases,
            "disagreement_cases": disagreement_cases,
            "missing_reference_cases": missing_reference_cases,
            "extra_prediction_cases": extra_prediction_cases,
        }

    @staticmethod
    def _build_candidate_union(channels: dict[str, dict[str, Any]], overall_limit: int) -> list[dict[str, Any]]:
        union: dict[int, dict[str, Any]] = {}
        channel_order = ("exact", "normalized", "fulltext", "embedding")
        for channel_name in channel_order:
            channel = channels.get(channel_name)
            if not channel:
                continue
            for item in channel.get("results", []):
                concept_id = int(item["concept_id"])
                record = union.setdefault(
                    concept_id,
                    {
                        "concept_id": concept_id,
                        "concept_name": item.get("concept_name"),
                        "concept_code": item.get("concept_code"),
                        "vocabulary_id": item.get("vocabulary_id"),
                        "domain_id": item.get("domain_id"),
                        "concept_class_id": item.get("concept_class_id"),
                        "standard_concept": item.get("standard_concept", item.get("is_standard")),
                        "retrieved_by": [],
                    },
                )
                record["retrieved_by"].append(channel_name)
                if "similarity" in item:
                    record["embedding_similarity"] = item["similarity"]
                if "ts_rank" in item:
                    record["fulltext_rank"] = item["ts_rank"]
        return list(union.values())[:overall_limit]

    @staticmethod
    def _summarise_edges(edges: dict[str, Any]) -> dict[str, Any]:
        counter: Counter[str] = Counter()
        for direction in ("outbound", "inbound"):
            for row in edges.get(direction, []):
                counter[row.get("predicate_kind", "UNKNOWN")] += 1
        return dict(sorted(counter.items()))

    @staticmethod
    def _mapping_source_key(row: dict[str, Any]) -> str | None:
        for key in ("source_key", "source_id", "source_term"):
            value = row.get(key)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _extract_predicted_ids(row: dict[str, Any], *, top_k: int | None) -> list[int]:
        raw = row.get("predicted_standard_concept_ids")
        if raw is None:
            raw = row.get("predicted_concept_ids", [])
        concept_ids = [int(value) for value in raw]
        if top_k is not None:
            return concept_ids[: max(1, top_k)]
        return concept_ids
