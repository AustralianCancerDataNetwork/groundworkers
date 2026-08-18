from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.tools.concept_tools import register_concept_tools
from groundworkers.tools.resolver_tools import register_resolver_tools


def _concept(concept_id: int, *, name: str = "Demo concept") -> dict:
    return {
        "concept_id": concept_id,
        "concept_name": name,
        "concept_code": str(concept_id),
        "vocabulary_id": "SNOMED",
        "domain_id": "Condition",
        "concept_class_id": "Clinical Finding",
        "standard_concept": True,
        "valid_start_date": "2000-01-01",
        "valid_end_date": "2099-12-31",
        "invalid_reason": None,
    }


def _ground_result(concept_id: int, *, name: str, score: float, match_kind: str = "EXACT") -> dict:
    return {
        "concept_id": concept_id,
        "concept_name": name,
        "vocabulary_id": "SNOMED",
        "domain_id": "Condition",
        "concept_class_id": "Clinical Finding",
        "standard_concept": True,
        "match_kind": match_kind,
        "matched_label": name,
        "total_score": score,
        "relevance": score,
        "parsimony_penalty": 0.0,
        "broadness_bonus": 0.01,
        "embedding_score": None,
        "separation": 0,
        "standardized_from": None,
    }


class StubGraphAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple | dict]] = []

    def get_concept(self, concept_id: int):
        self.calls.append(("get_concept", (concept_id,)))
        if concept_id == 999:
            return None
        return _concept(concept_id)

    def get_concept_by_code(self, vocabulary_id: str, code: str):
        self.calls.append(("get_concept_by_code", (vocabulary_id, code)))
        if code == "missing":
            return []
        return [_concept(123, name="Matched concept")]

    def get_ancestors(self, concept_id: int, max_depth: int):
        self.calls.append(("get_ancestors", {"concept_id": concept_id, "max_depth": max_depth}))
        return [
            {
                "concept_id": 2,
                "concept_name": "Parent",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "standard_concept": True,
                "depth": 1,
            },
            {
                "concept_id": 3,
                "concept_name": "Grandparent",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "standard_concept": True,
                "depth": 2,
            },
        ]

    def get_descendants(self, concept_id: int, max_depth: int):
        self.calls.append(("get_descendants", {"concept_id": concept_id, "max_depth": max_depth}))
        return []

    def map_to_standard(self, vocabulary_id: str, code: str) -> dict:
        self.calls.append(("map_to_standard", (vocabulary_id, code)))
        if code == "unknown":
            raise GroundworkersError("NOT_FOUND", f"Concept {vocabulary_id}:{code} was not found")
        source = _concept(123, name="Source concept")
        return {"source": source, "standard_concepts": [_concept(456, name="Standard concept")]}

    def get_edges(self, concept_id: int) -> dict:
        self.calls.append(("get_edges", (concept_id,)))
        if concept_id == 999:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        return {
            "outbound": [
                {
                    "relationship_id": "Is a",
                    "predicate_kind": "HIERARCHY",
                    "target_concept_id": 200,
                    "target_concept_name": "Diabetes mellitus",
                    "valid": True,
                }
            ],
            "inbound": [
                {
                    "relationship_id": "Subsumes",
                    "predicate_kind": "HIERARCHY",
                    "source_concept_id": 50,
                    "source_concept_name": "T2DM subtype",
                    "valid": True,
                }
            ],
        }

    def get_associations(self, concept_id, *, direction="out", predicate_subkinds=None, active_only=True, limit=50):
        self.calls.append(("get_associations", {
            "concept_id": concept_id, "direction": direction,
            "predicate_subkinds": predicate_subkinds, "active_only": active_only, "limit": limit,
        }))
        if concept_id == 999:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        result = {"concept_id": concept_id, "predicate_kind": "Association"}
        if direction in ("out", "both"):
            result["outbound"] = [{
                "relationship_id": "Has cytotox chemo Rx", "predicate_subkind": "Therapeutic",
                "target_concept_id": 955632, "target_concept_name": "fluorouracil",
                "vocabulary_id": "RxNorm", "domain_id": "Drug", "concept_class_id": "Ingredient",
                "standard_concept": "S", "valid": True,
            }]
        if direction in ("in", "both"):
            result["inbound"] = []
        return result

    def get_extended_inheritance(self, concept_id, *, direction="out", predicate_subkinds=None, active_only=True, limit=50):
        self.calls.append(("get_extended_inheritance", {
            "concept_id": concept_id, "direction": direction,
            "predicate_subkinds": predicate_subkinds, "active_only": active_only, "limit": limit,
        }))
        if concept_id == 999:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        result = {"concept_id": concept_id, "predicate_kind": "Hierarchy"}
        if direction in ("out", "both"):
            result["outbound"] = [{
                "relationship_id": "Is a", "predicate_subkind": "Taxonomic – up",
                "target_concept_id": 200, "target_concept_name": "Diabetes mellitus",
                "vocabulary_id": "SNOMED", "domain_id": "Condition", "concept_class_id": "Clinical Finding",
                "standard_concept": True, "valid": True,
            }]
        if direction in ("in", "both"):
            result["inbound"] = []
        return result

    def get_neighbors(
        self,
        concept_id: int,
        max_depth: int,
        predicate_kinds: list[str] | None,
        max_nodes: int,
        include_edges: bool,
    ) -> dict:
        self.calls.append(("get_neighbors", {
            "concept_id": concept_id,
            "max_depth": max_depth,
            "predicate_kinds": predicate_kinds,
            "max_nodes": max_nodes,
            "include_edges": include_edges,
        }))
        if concept_id == 999:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        if predicate_kinds and "BADKIND" in predicate_kinds:
            raise GroundworkersError("INVALID_INPUT", "Unknown predicate_kind 'BADKIND'. Valid values: ...")
        neighbors = [
            {"concept_id": 200, "concept_name": "Diabetes mellitus", "vocabulary_id": "SNOMED",
             "domain_id": "Condition", "concept_class_id": "Clinical Finding", "standard_concept": True},
            {"concept_id": 201, "concept_name": "T2DM with complication", "vocabulary_id": "SNOMED",
             "domain_id": "Condition", "concept_class_id": "Clinical Finding", "standard_concept": True},
        ]
        edges = [
            {"subject_id": concept_id, "predicate_id": "Is a", "predicate_kind": "HIERARCHY", "object_id": 200},
        ] if include_edges else []
        return {
            "concept_id": concept_id,
            "neighbor_count": len(neighbors),
            "edge_count": len(edges),
            "neighbors": neighbors,
            "edges": edges,
            "terminated_early": False,
            "terminated_reason": None,
        }

    def find_path(self, source_id: int, target_id: int, max_depth: int, within_domain: bool = True) -> dict:
        self.calls.append(("find_path", {"source_id": source_id, "target_id": target_id, "max_depth": max_depth}))
        if source_id == target_id:
            return {"found": True, "paths": [{"length": 0, "steps": []}]}
        if source_id == 999 or target_id == 999:
            return {"found": False, "paths": []}
        return {
            "found": True,
            "paths": [
                {
                    "length": 1,
                    "steps": [
                        {
                            "subject_id": source_id,
                            "subject_name": "Source",
                            "predicate": "Is a",
                            "predicate_kind": "HIERARCHY",
                            "object_id": target_id,
                            "object_name": "Target",
                        }
                    ],
                }
            ],
        }


class StubGroundingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

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
    ) -> dict:
        self.calls.append(("ground", {
            "query": query,
            "limit": limit,
            "domain": domain,
            "vocabulary_id": vocabulary_id,
            "parent_ids": parent_ids,
            "standard_only": standard_only,
            "active_only": active_only,
        }))
        all_results = [
            _ground_result(100, name="Type 2 diabetes mellitus", score=1.0, match_kind="EXACT"),
            _ground_result(101, name="Diabetes mellitus type 2", score=0.8, match_kind="PARTIAL"),
        ]
        return {
            "results": all_results[:limit],
            "grounding_explanation": {
                "matched_tier": all_results[0]["match_kind"],
                "used_embedding": False,
                "effective_parent_ids": list(parent_ids) if parent_ids else [],
                "parent_ids_source": "explicit" if parent_ids else "none",
                "standard_only": standard_only,
                "active_only": active_only,
            },
        }


def build_server(adapter, grounding_service=None) -> GroundcrewServer:
    server = GroundcrewServer("test-server")
    register_concept_tools(server, adapter)
    register_resolver_tools(server, grounding_service or StubGroundingService())
    return server


def test_concept_get_with_valid_id_returns_expected_shape():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_get", concept_id=123)

    assert result["concept_id"] == 123
    assert result["concept_name"] == "Demo concept"
    assert adapter.calls == [("get_concept", (123,))]


def test_concept_get_with_unknown_id_returns_not_found_error():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_get", concept_id=999)

    assert result == {
        "error": True,
        "code": "NOT_FOUND",
        "message": "Concept 999 was not found",
    }


def test_concept_ancestors_returns_depth_annotated_list():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_ancestors", concept_id=123, max_depth=5)

    assert result["concept_id"] == 123
    assert [item["depth"] for item in result["ancestors"]] == [1, 2]
    assert adapter.calls == [("get_ancestors", {"concept_id": 123, "max_depth": 5})]


def test_concept_descendants_leaf_returns_empty_list():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_descendants", concept_id=321, max_depth=3)

    assert result == {"concept_id": 321, "descendants": []}
    assert adapter.calls == [("get_descendants", {"concept_id": 321, "max_depth": 3})]


def test_concept_descendants_clamps_max_depth_to_ten():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    server.call("concept_descendants", concept_id=321, max_depth=100)

    assert adapter.calls == [("get_descendants", {"concept_id": 321, "max_depth": 10})]


def test_concept_ancestors_clamps_max_depth_to_twenty():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    server.call("concept_ancestors", concept_id=123, max_depth=999)

    assert adapter.calls == [("get_ancestors", {"concept_id": 123, "max_depth": 20})]


def test_concept_get_rejects_non_positive_id():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_get", concept_id=0)

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert adapter.calls == []


def test_concept_by_code_rejects_empty_vocabulary_id():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_by_code", vocabulary_id="  ", concept_code="C34")

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert adapter.calls == []


def test_concept_by_code_always_returns_list_shape():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_by_code", vocabulary_id="SNOMED", concept_code="valid")

    assert "concepts" in result
    assert isinstance(result["concepts"], list)
    assert result["concepts"][0]["concept_name"] == "Matched concept"


# ---------------------------------------------------------------------------
# concept_ground — updated interface
# ---------------------------------------------------------------------------

def test_concept_ground_empty_query_returns_invalid_input():
    adapter = StubGraphAdapter()
    grounding = StubGroundingService()
    server = build_server(adapter, grounding)

    result = server.call("concept_ground", query="   ")

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert grounding.calls == []


def test_concept_ground_valid_query_returns_results_and_explanation():
    adapter = StubGraphAdapter()
    server = build_server(adapter, StubGroundingService())

    result = server.call("concept_ground", query="diabetes", limit=5)

    assert "results" in result
    assert "grounding_explanation" in result
    explanation = result["grounding_explanation"]
    assert "matched_tier" in explanation
    assert "used_embedding" in explanation
    assert "effective_parent_ids" in explanation
    assert "parent_ids_source" in explanation


def test_concept_ground_results_include_scoring_fields():
    adapter = StubGraphAdapter()
    server = build_server(adapter, StubGroundingService())

    result = server.call("concept_ground", query="diabetes", limit=5)

    for r in result["results"]:
        assert "total_score" in r
        assert "relevance" in r
        assert "parsimony_penalty" in r
        assert "broadness_bonus" in r
        assert "separation" in r
        assert "embedding_score" in r
        assert "matched_label" in r
        assert "standardized_from" in r


def test_concept_ground_results_sorted_by_score():
    adapter = StubGraphAdapter()
    server = build_server(adapter, StubGroundingService())

    result = server.call("concept_ground", query="diabetes", limit=5)

    scores = [r["total_score"] for r in result["results"]]
    assert scores == sorted(scores, reverse=True)


def test_concept_ground_exact_match_appears_first():
    adapter = StubGraphAdapter()
    server = build_server(adapter, StubGroundingService())

    result = server.call("concept_ground", query="Type 2 diabetes mellitus", limit=5)

    assert result["results"][0]["match_kind"] == "EXACT"


def test_concept_ground_passes_parent_ids_to_adapter():
    adapter = StubGraphAdapter()
    grounding = StubGroundingService()
    server = build_server(adapter, grounding)

    server.call("concept_ground", query="diabetes", parent_ids=[4002649, 443238])

    call = grounding.calls[0]
    assert call[0] == "ground"
    assert call[1]["parent_ids"] == (4002649, 443238)


def test_concept_ground_explanation_reflects_explicit_parent_ids():
    adapter = StubGraphAdapter()
    server = build_server(adapter, StubGroundingService())

    result = server.call("concept_ground", query="diabetes", parent_ids=[4002649])

    assert result["grounding_explanation"]["parent_ids_source"] == "explicit"
    assert 4002649 in result["grounding_explanation"]["effective_parent_ids"]


def test_concept_ground_rejects_non_positive_parent_ids():
    adapter = StubGraphAdapter()
    grounding = StubGroundingService()
    server = build_server(adapter, grounding)

    result = server.call("concept_ground", query="diabetes", parent_ids=[4002649, -1])

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert grounding.calls == []


def test_concept_ground_without_parent_ids_passes_none_to_adapter():
    adapter = StubGraphAdapter()
    grounding = StubGroundingService()
    server = build_server(adapter, grounding)

    server.call("concept_ground", query="diabetes")

    call = grounding.calls[0]
    assert call[1]["parent_ids"] is None


# ---------------------------------------------------------------------------
# concept_neighbors
# ---------------------------------------------------------------------------

def test_concept_neighbors_returns_expected_shape():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_neighbors", concept_id=100)

    assert result["concept_id"] == 100
    assert "neighbors" in result
    assert "edges" in result
    assert "neighbor_count" in result
    assert "edge_count" in result
    assert "terminated_early" in result
    assert "terminated_reason" in result


def test_concept_neighbors_unknown_concept_returns_not_found():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_neighbors", concept_id=999)

    assert result["error"] is True
    assert result["code"] == "NOT_FOUND"


def test_concept_neighbors_rejects_non_positive_id():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_neighbors", concept_id=0)

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert adapter.calls == []


def test_concept_neighbors_clamps_max_depth_to_four():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    server.call("concept_neighbors", concept_id=100, max_depth=99)

    call = adapter.calls[0]
    assert call[1]["max_depth"] == 4


def test_concept_neighbors_clamps_max_nodes():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    server.call("concept_neighbors", concept_id=100, max_nodes=10000)

    call = adapter.calls[0]
    assert call[1]["max_nodes"] == 500


def test_concept_neighbors_passes_predicate_kinds_to_adapter():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    server.call("concept_neighbors", concept_id=100, predicate_kinds=["HIERARCHY"])

    call = adapter.calls[0]
    assert call[1]["predicate_kinds"] == ["HIERARCHY"]


def test_concept_neighbors_invalid_predicate_kind_returns_error():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_neighbors", concept_id=100, predicate_kinds=["BADKIND"])

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"


def test_concept_neighbors_include_edges_false_passes_through():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_neighbors", concept_id=100, include_edges=False)

    call = adapter.calls[0]
    assert call[1]["include_edges"] is False
    assert result["edges"] == []


# ---------------------------------------------------------------------------
# Existing edge/path/map tests (unchanged)
# ---------------------------------------------------------------------------

def test_concept_relationships_unknown_concept_returns_not_found():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_relationships", concept_id=999)

    assert result["error"] is True
    assert result["code"] == "NOT_FOUND"
    assert adapter.calls == [("get_edges", (999,))]


_VALID_PREDICATE_KINDS = {"HIERARCHY", "IDENTITY", "COMPOSITION", "ASSOCIATION", "ATTRIBUTE"}


def test_concept_relationships_known_concept_returns_edge_structure():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_relationships", concept_id=123)

    assert result["concept_id"] == 123
    assert "inbound" in result
    assert "outbound" in result
    for edge in result["outbound"]:
        assert edge["predicate_kind"] in _VALID_PREDICATE_KINDS
    for edge in result["inbound"]:
        assert edge["predicate_kind"] in _VALID_PREDICATE_KINDS


def test_concept_path_same_source_and_target_returns_empty_steps():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_path", source_id=123, target_id=123)

    assert result["found"] is True
    assert len(result["paths"]) == 1
    assert result["paths"][0]["steps"] == []
    assert result["paths"][0]["length"] == 0


def test_concept_path_no_path_found_returns_found_false():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_path", source_id=1, target_id=999)

    assert result["found"] is False
    assert result["paths"] == []


def test_concept_map_to_standard_unknown_code_returns_not_found():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_map_to_standard", vocabulary_id="ICD10CM", concept_code="unknown")

    assert result["error"] is True
    assert result["code"] == "NOT_FOUND"


def test_concept_map_to_standard_returns_source_and_standard_concepts():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_map_to_standard", vocabulary_id="ICD10CM", concept_code="E11.9")

    assert "source" in result
    assert "standard_concepts" in result
    assert isinstance(result["standard_concepts"], list)


# --- concept_associations ---

def test_concept_associations_returns_expected_shape():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_associations", concept_id=35806596)

    assert result["concept_id"] == 35806596
    assert result["predicate_kind"] == "Association"
    assert result["outbound"][0]["relationship_id"] == "Has cytotox chemo Rx"
    assert result["outbound"][0]["standard_concept"] == "S"
    assert adapter.calls[0][0] == "get_associations"


def test_concept_associations_passes_filters_and_clamps_limit():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    server.call(
        "concept_associations",
        concept_id=1,
        direction="both",
        predicate_subkinds=["Therapeutic"],
        active_only=False,
        limit=10000,
    )

    call = adapter.calls[0][1]
    assert call["direction"] == "both"
    assert call["predicate_subkinds"] == ["Therapeutic"]
    assert call["active_only"] is False
    assert call["limit"] == 200


def test_concept_associations_rejects_bad_direction():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_associations", concept_id=1, direction="sideways")

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert adapter.calls == []


def test_concept_associations_rejects_non_positive_id():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_associations", concept_id=0)

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert adapter.calls == []


def test_concept_associations_unknown_concept_returns_not_found():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_associations", concept_id=999)

    assert result["error"] is True
    assert result["code"] == "NOT_FOUND"


# --- concept_extended_inheritance (distinct from strict concept_ancestor traversal) ---

def test_concept_extended_inheritance_uses_its_own_method_not_ancestors():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_extended_inheritance", concept_id=1)

    assert result["predicate_kind"] == "Hierarchy"
    assert result["outbound"][0]["predicate_subkind"] == "Taxonomic – up"
    called = [c[0] for c in adapter.calls]
    assert "get_extended_inheritance" in called
    # It must NOT fall back to the concept_ancestor closure.
    assert "get_ancestors" not in called


def test_concept_extended_inheritance_rejects_bad_direction():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_extended_inheritance", concept_id=1, direction="up")

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"
    assert adapter.calls == []


def test_concept_extended_inheritance_unknown_concept_returns_not_found():
    adapter = StubGraphAdapter()
    server = build_server(adapter)

    result = server.call("concept_extended_inheritance", concept_id=999)

    assert result["error"] is True
    assert result["code"] == "NOT_FOUND"
