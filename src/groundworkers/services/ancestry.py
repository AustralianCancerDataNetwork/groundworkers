"""Transport-neutral constrained selection over complete stored ancestry."""

from dataclasses import dataclass, field
from typing import Any

from groundworkers.base.ancestry import SharedAncestorRequest
from groundworkers.base.errors import GroundworkersError
from groundworkers.services.graph import GraphService


def _eligible(
    row: dict[str, Any], domain: str | None, request: SharedAncestorRequest
) -> bool:
    return bool(
        row.get("standard_concept")
        and row.get("is_active", True)
        and row.get("domain_id") == domain
        and (
            request.concept_class_ids is None
            or row.get("concept_class_id") in request.concept_class_ids
        )
        and (
            request.vocabulary_ids is None
            or row.get("vocabulary_id") in request.vocabulary_ids
        )
    )


def resolve_shared_ancestor(
    graph: GraphService, request: SharedAncestorRequest
) -> dict[str, Any]:
    ids = request.concept_ids
    graph.validate_ancestor_constraints(
        request.concept_class_ids, request.vocabulary_ids
    )
    ancestry = graph.get_stored_ancestor_maps(ids, request.max_depth)
    domains = {str(cid): ancestry[cid][cid].get("domain_id") for cid in ids}
    domain_set = set(domains.values())
    common = []
    maps = []
    status, reason = "incompatible_inputs", "incompatible_domains"
    if None not in domain_set and len(domain_set) == 1:
        domain = next(iter(domain_set))
        maps = [
            {
                aid: row
                for aid, row in ancestry[cid].items()
                if _eligible(row, domain, request)
            }
            for cid in ids
        ]
        shared_ids = set.intersection(*(set(m) for m in maps))
        common = sorted(
            (
                {**maps[0][aid], "depth": max(m[aid]["depth"] for m in maps)}
                for aid in shared_ids
            ),
            key=lambda row: (row["depth"], row["concept_id"]),
        )
        status, reason = "no_match", "no_shared_standard_ancestor"
    nearest = (
        [row for row in common if row["depth"] == common[0]["depth"]] if common else []
    )
    selected = nearest[0] if len(nearest) == 1 else None
    if selected:
        status, reason = "selected", "nearest_shared_standard_ancestor"
    elif nearest:
        status, reason = "ambiguous", "multiple_nearest_shared_ancestors"
    page = common[request.offset : request.offset + request.limit]
    return {
        "status": status,
        "found": selected is not None,
        "concept_ids": ids,
        "input_concept_ids": ids,
        "input_domains": domains,
        "constraints": {
            "concept_class_ids": request.concept_class_ids,
            "vocabulary_ids": request.vocabulary_ids,
            "standard_only": True,
            "active_only": True,
            "same_domain": True,
        },
        "ancestry_source": "concept_ancestor",
        "distance_metric": "min_levels_of_separation",
        "max_depth": request.max_depth,
        "include_self": True,
        "selection_complete": True,
        "selection_reason": reason,
        "selected_ancestor": selected,
        "selected_distances_by_input": {
            str(cid): m[selected["concept_id"]]["depth"]
            for cid, m in zip(ids, maps, strict=True)
        }
        if selected
        else {},
        "common_ancestors": page,
        "common_candidate_count": len(common),
        "nearest_candidate_count": len(nearest),
        "page": {
            "offset": request.offset,
            "limit": request.limit,
            "returned": len(page),
            "has_more": request.offset + len(page) < len(common),
        },
    }


@dataclass
class NearestAncestorDecision:
    selected: dict[str, Any] | None
    alternatives: list[dict[str, Any]]
    reason: str
    nearest_count: int
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.selected is not None:
            return "selected"
        return "ambiguous" if self.nearest_count > 1 else "no_match"

    @property
    def evidence_route(self) -> str:
        return "maps_to" if "maps_to" in self.reason else "ancestry"


def _nearest_decision(
    rows: list[dict[str, Any]], reason: str
) -> NearestAncestorDecision:
    ordered = sorted(rows, key=lambda row: (row["depth"], row["concept_id"]))
    nearest = [row for row in ordered if row["depth"] == ordered[0]["depth"]]
    selected = nearest[0] if len(nearest) == 1 else None
    return NearestAncestorDecision(
        selected,
        [row for row in ordered if row is not selected][:5],
        reason if selected else "multiple_nearest_standard_ancestors",
        len(nearest),
    )


def _mapped_decision(
    graph: GraphService, seed: dict[str, Any]
) -> NearestAncestorDecision:
    mapped = graph.map_to_standard(seed["vocabulary_id"], seed["concept_code"])
    targets = {
        row["concept_id"]: row
        for row in mapped.get("standard_concepts", [])
        if row.get("standard_concept") and row.get("is_active", True)
    }
    ordered = [targets[cid] for cid in sorted(targets)]
    if len(ordered) == 1:
        return NearestAncestorDecision(ordered[0], [], "maps_to_standard", 1)
    if ordered:
        return NearestAncestorDecision(
            None, ordered[:5], "multiple_maps_to_targets", len(ordered)
        )
    return NearestAncestorDecision(
        None,
        [],
        "no_standard_ancestor",
        0,
        [
            "No active standard ancestor within max_depth and no eligible Maps-to target."
        ],
    )


def resolve_nearest_target(
    graph: GraphService,
    seed: dict[str, Any],
    max_depth: int,
    reason: str,
) -> NearestAncestorDecision:
    if seed.get("standard_concept") and seed.get("is_active", True):
        return NearestAncestorDecision({**seed, "depth": 0}, [], reason, 1)
    ancestors = graph.get_ancestors(seed["concept_id"], max_depth)
    eligible = [
        row
        for row in ancestors
        if row.get("standard_concept")
        and row.get("is_active", True)
        and row.get("domain_id") == seed.get("domain_id")
    ]
    if eligible:
        return _nearest_decision(eligible, reason)
    return _mapped_decision(graph, seed)


def ancestor_path_evidence(
    graph: GraphService,
    seed: dict[str, Any],
    decision: NearestAncestorDecision,
    max_depth: int,
) -> dict[str, Any]:
    result = {
        "path_to_parent": [],
        "path_status": "not_applicable",
        "path_error": None,
        "warnings": [],
    }
    target = decision.selected
    if target is None or target["concept_id"] == seed["concept_id"]:
        return result
    if decision.evidence_route == "maps_to":
        result["path_status"] = "maps_to_evidence"
        return result
    try:
        info = graph.find_path(
            seed["concept_id"], target["concept_id"], max_depth, within_domain=True
        )
        result["path_to_parent"] = info.get("paths", [])
        result["path_status"] = "found" if result["path_to_parent"] else "not_found"
    except GroundworkersError as exc:
        result["path_status"], result["path_error"] = "unavailable", exc.to_dict()
    if not result["path_to_parent"]:
        result["warnings"].append(
            "Stored ancestor evidence is valid; no bounded relationship path was produced."
        )
    return result
