from pathlib import Path
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.bootstrap import build_app_config
from groundworkers.server import build_adapters
from groundworkers.services.grounding import GroundingService


def _load_graph_adapter():
    try:
        import omop_graph  # noqa: F401
    except ImportError:
        pytest.skip("omop_graph is not installed in this environment")

    config_path = os.getenv("GROUNDWORKERS_CONFIG_PATH") or os.getenv("GROUNDWORKERS_CONFIG")
    profile = os.getenv("GROUNDWORKERS_PROFILE")
    try:
        config = build_app_config(config_path=config_path, profile=profile)
    except (FileNotFoundError, ValueError) as exc:
        pytest.skip(f"shared stack config is unavailable: {exc}")
    if config.omop_graph is None or config.cdm_engine is None:
        pytest.skip("omop_graph is not configured in the selected stack config")
    adapter = build_adapters(config).omop_graph
    if adapter is None:
        pytest.skip("omop_graph adapter was not built")
    if not adapter.is_available():
        pytest.skip("omop_graph backend is not available in this environment")
    return adapter


@pytest.mark.integration
def test_get_concept_by_known_id():
    adapter = _load_graph_adapter()

    concept = adapter.get_concept(201826)

    assert concept is not None
    assert concept["concept_name"] == "Type 2 diabetes mellitus"


@pytest.mark.integration
def test_get_concept_returns_correct_fields():
    adapter = _load_graph_adapter()

    concept = adapter.get_concept(201826)

    assert concept is not None
    assert set(concept) == {
        "concept_id",
        "concept_name",
        "concept_code",
        "vocabulary_id",
        "domain_id",
        "concept_class_id",
        "standard_concept",
        "valid_start_date",
        "valid_end_date",
        "invalid_reason",
    }


@pytest.mark.integration
def test_ancestors_depth_is_monotonically_increasing():
    adapter = _load_graph_adapter()

    ancestors = adapter.get_ancestors(201826, max_depth=5)

    depths = [item["depth"] for item in ancestors]
    assert depths == sorted(depths)


@pytest.mark.integration
def test_descendants_returns_list_and_respects_depth():
    adapter = _load_graph_adapter()

    # 201826 has descendants; this test verifies shape and depth constraint.
    # A true leaf concept is vocab-specific — use depth=0 equivalent via max_depth=1
    # and verify every result has depth <= 1.
    descendants = adapter.get_descendants(201826, max_depth=1)

    assert isinstance(descendants, list)
    assert all(item["depth"] <= 1 for item in descendants)


@pytest.mark.integration
def test_concept_by_code_snomed():
    adapter = _load_graph_adapter()

    concepts = adapter.get_concept_by_code("SNOMED", "44054006")

    assert concepts
    assert concepts[0]["vocabulary_id"] == "SNOMED"


@pytest.mark.integration
def test_ground_exact_match():
    adapter = _load_graph_adapter()
    service = GroundingService(adapter)

    result = service.ground("Type 2 diabetes mellitus", limit=5, domain=None, vocabulary_id=None)

    assert "results" in result
    assert "grounding_explanation" in result
    results = result["results"]
    assert results
    assert results[0]["match_kind"] == "EXACT"
    assert results[0]["concept_name"].lower() == "type 2 diabetes mellitus"
    assert results[0]["standard_concept"] is True


@pytest.mark.integration
def test_ground_partial_match():
    adapter = _load_graph_adapter()
    service = GroundingService(adapter)

    result = service.ground("type 2 diabet", limit=5, domain=None, vocabulary_id=None)

    results = result["results"]
    assert results
    assert all(r["standard_concept"] is True for r in results)


@pytest.mark.integration
def test_ground_returns_standard_concepts_only():
    adapter = _load_graph_adapter()
    service = GroundingService(adapter)

    result = service.ground("diabetes", limit=10, domain=None, vocabulary_id=None)

    results = result["results"]
    assert results
    assert all(r["standard_concept"] is True for r in results)
    scores = [r["total_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.integration
def test_get_edges_returns_predicate_kinds():
    adapter = _load_graph_adapter()

    edges = adapter.get_edges(201826)

    assert "outbound" in edges
    assert "inbound" in edges
    valid_kinds = {"HIERARCHY", "IDENTITY", "COMPOSITION", "ASSOCIATION", "ATTRIBUTE"}
    for edge in edges["outbound"]:
        assert edge["predicate_kind"] in valid_kinds
        assert "relationship_id" in edge
        assert "target_concept_id" in edge


@pytest.mark.integration
def test_find_path_between_concept_and_its_ancestor():
    adapter = _load_graph_adapter()

    ancestors = adapter.get_ancestors(201826, max_depth=1)
    if not ancestors:
        pytest.skip("concept has no ancestors in this dataset")
    parent_id = ancestors[0]["concept_id"]

    result = adapter.find_path(201826, parent_id, max_depth=5)

    assert result["found"] is True
    assert len(result["paths"]) > 0
    path = result["paths"][0]
    assert path["length"] == len(path["steps"])
    if path["steps"]:
        assert path["steps"][0]["subject_id"] == 201826
        assert path["steps"][-1]["object_id"] == parent_id


@pytest.mark.integration
def test_map_icd_code_to_standard_snomed():
    adapter = _load_graph_adapter()

    result = adapter.map_to_standard("ICD10CM", "E11.9")

    assert "source" in result
    assert "standard_concepts" in result
    assert result["source"]["vocabulary_id"] == "ICD10CM"
    assert result["source"]["concept_code"] == "E11.9"
    assert all(c["standard_concept"] is True for c in result["standard_concepts"])
