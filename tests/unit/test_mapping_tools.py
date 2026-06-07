from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.tools.mapping_tools import register_mapping_tools


class StubMappingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def concept_search_normalized(self, query: str, **kwargs):
        self.calls.append(("concept_search_normalized", {"query": query, **kwargs}))
        return {"query": query.strip(), "normalized_query": "demo", "results": []}

    def concept_candidate_bundle(self, query: str, **kwargs):
        self.calls.append(("concept_candidate_bundle", {"query": query, **kwargs}))
        return {"query": query.strip(), "channels": {}, "candidate_union": [], "warnings": []}

    def concept_nearest_standard_ancestor(self, **kwargs):
        self.calls.append(("concept_nearest_standard_ancestor", kwargs))
        return {"found": True, "selected_parent": {"concept_id": 1}}

    def concept_mapping_context(self, concept_id: int, **kwargs):
        self.calls.append(("concept_mapping_context", {"concept_id": concept_id, **kwargs}))
        return {"concept": {"concept_id": concept_id}}

    def concept_map_to_value(self, vocabulary_id: str, concept_code: str):
        self.calls.append(("concept_map_to_value", {"vocabulary_id": vocabulary_id, "concept_code": concept_code}))
        return {"source_concept": {"concept_id": 1}, "maps_to_value": []}

    def concept_resolve_mapping_expression(self, items, **kwargs):
        self.calls.append(("concept_resolve_mapping_expression", {"items": items, **kwargs}))
        return {"resolved_concept_ids": []}

    def mapping_evaluate_candidates(self, predicted_mappings, reference_mappings, **kwargs):
        self.calls.append(("mapping_evaluate_candidates", {"predicted_mappings": predicted_mappings, "reference_mappings": reference_mappings, **kwargs}))
        return {"summary_metrics": {"accuracy": 1.0}}


def build_server(service=None) -> GroundcrewServer:
    server = GroundcrewServer("test-server")
    register_mapping_tools(server, service or StubMappingService())
    return server


def test_concept_search_normalized_clamps_limit_and_calls_service():
    service = StubMappingService()
    server = build_server(service)

    result = server.call("concept_search_normalized", query=" Diabetes ", limit=500)

    assert result["normalized_query"] == "demo"
    assert service.calls == [
        (
            "concept_search_normalized",
            {
                "query": " Diabetes ",
                "domain": None,
                "vocabulary_id": None,
                "standard_only": False,
                "include_synonyms": False,
                "normalization_profile": "verbatim",
                "remove_stop_phrases": True,
                "limit": 50,
            },
        )
    ]


def test_concept_candidate_bundle_rejects_invalid_input_from_service():
    class InvalidService(StubMappingService):
        def concept_candidate_bundle(self, query: str, **kwargs):
            raise ValueError("query must be a non-empty string")

    server = build_server(InvalidService())

    result = server.call("concept_candidate_bundle", query="   ")

    assert result == {"error": True, "code": "INVALID_INPUT", "message": "query must be a non-empty string"}


def test_concept_nearest_standard_ancestor_returns_groundworkers_error_dict():
    class ErrorService(StubMappingService):
        def concept_nearest_standard_ancestor(self, **kwargs):
            raise GroundworkersError("NOT_FOUND", "missing")

    server = build_server(ErrorService())

    result = server.call("concept_nearest_standard_ancestor", concept_id=999)

    assert result == {"error": True, "code": "NOT_FOUND", "message": "missing"}


def test_concept_mapping_context_clamps_limits_before_calling_service():
    service = StubMappingService()
    server = build_server(service)

    server.call(
        "concept_mapping_context",
        concept_id=123,
        ancestor_limit=500,
        descendant_limit=0,
        neighbor_limit=500,
        embedding_neighbor_limit=0,
    )

    assert service.calls == [
        (
            "concept_mapping_context",
            {
                "concept_id": 123,
                "include_standard_mapping": True,
                "include_ancestors": True,
                "include_descendants": False,
                "include_relationship_summary": True,
                "include_neighbors": True,
                "include_embedding_neighbors": False,
                "ancestor_limit": 10,
                "descendant_limit": 1,
                "neighbor_limit": 100,
                "embedding_neighbor_limit": 1,
                "model_name": None,
            },
        )
    ]


def test_mapping_evaluate_candidates_calls_service():
    service = StubMappingService()
    server = build_server(service)

    result = server.call("mapping_evaluate_candidates", predicted_mappings=[], reference_mappings=[])

    assert result["summary_metrics"]["accuracy"] == 1.0
    assert service.calls[0][0] == "mapping_evaluate_candidates"
