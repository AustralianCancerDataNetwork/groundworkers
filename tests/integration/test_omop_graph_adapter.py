import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.app import build_adapters
from groundworkers.bootstrap import build_app_config
from groundworkers.services.graph import GraphService
from groundworkers.services.grounding import ConceptGroundingService


def _load_graph_adapter():
    try:
        import omop_graph  # noqa: F401
    except ImportError:
        pytest.skip("omop_graph is not installed in this environment")

    config_path = os.getenv("GROUNDWORKERS_CONFIG_PATH") or os.getenv(
        "GROUNDWORKERS_CONFIG"
    )
    try:
        config = build_app_config(config_path=config_path)
    except (FileNotFoundError, ValueError) as exc:
        pytest.skip(f"shared stack config is unavailable: {exc}")
    if "omop_graph" not in config.stack.tools:
        pytest.skip("omop_graph is not configured in the selected stack config")
    adapter = build_adapters(config).omop_graph
    if adapter is None:
        pytest.skip("omop_graph adapter was not built")
    if not adapter.is_available():
        pytest.skip("omop_graph backend is not available in this environment")
    return adapter


def _find_nonstandard_condition_term(adapter) -> str:
    stmt = text(
        f"""
        SELECT c.concept_name
        FROM {adapter.vocab_schema}.concept AS c
        JOIN {adapter.vocab_schema}.concept_relationship AS cr
          ON cr.concept_id_1 = c.concept_id
         AND lower(cr.relationship_id) = 'maps to'
         AND cr.invalid_reason IS NULL
        JOIN {adapter.vocab_schema}.concept AS s
          ON s.concept_id = cr.concept_id_2
         AND s.standard_concept = 'S'
         AND s.domain_id = 'Condition'
         AND s.invalid_reason IS NULL
        WHERE c.domain_id = 'Condition'
          AND c.standard_concept IS NULL
          AND c.invalid_reason IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM {adapter.vocab_schema}.concept AS c2
              WHERE lower(c2.concept_name) = lower(c.concept_name)
                AND c2.domain_id = 'Condition'
                AND c2.standard_concept IN ('S', 'C')
                AND c2.invalid_reason IS NULL
          )
        ORDER BY c.concept_id
        LIMIT 1
        """
    )
    with adapter.engine.connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        pytest.skip(
            "no uniquely named non-standard Condition concept with a standard mapping was found"
        )
    return str(row[0])


def _skip_if_parent_lookup_is_unindexed(adapter) -> None:
    """Skip hierarchy smoke checks when the live vocabulary DB is not shaped for them."""

    stmt = text(
        """
        SELECT indexdef
        FROM pg_indexes
        WHERE schemaname = :schema
          AND tablename = 'concept_ancestor'
        """
    )
    try:
        with adapter.engine.connect() as conn:
            rows = conn.execute(stmt, {"schema": adapter.vocab_schema}).scalars().all()
    except Exception as exc:  # noqa: BLE001 - integration environment preflight
        pytest.skip(f"could not inspect concept_ancestor indexes: {exc}")

    if not any("(descendant_concept_id" in indexdef.lower() for indexdef in rows):
        pytest.skip(
            "concept_ancestor lacks a descendant-leading index; "
            "parent hierarchy lookup is an environment smoke test, not an application regression test"
        )


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
    _skip_if_parent_lookup_is_unindexed(adapter)
    graph = GraphService(adapter)

    ancestors = graph.get_ancestors(201826, max_depth=5)

    depths = [item["depth"] for item in ancestors]
    assert depths == sorted(depths)


@pytest.mark.integration
def test_descendants_returns_list_and_respects_depth():
    adapter = _load_graph_adapter()
    graph = GraphService(adapter)

    # 201826 has descendants; this test verifies shape and depth constraint.
    # A true leaf concept is vocab-specific — use depth=0 equivalent via max_depth=1
    # and verify every result has depth <= 1.
    descendants = graph.get_descendants(201826, max_depth=1)

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
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground(
        "Type 2 diabetes mellitus", limit=5, domain=None, vocabulary_id=None
    )

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
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("type 2 diabet", limit=5, domain=None, vocabulary_id=None)

    results = result["results"]
    assert results
    assert all(r["standard_concept"] is True for r in results)


@pytest.mark.integration
def test_ground_returns_standard_concepts_only():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("diabetes", limit=10, domain=None, vocabulary_id=None)

    results = result["results"]
    assert results
    assert all(r["standard_concept"] is True for r in results)
    scores = [r["total_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.integration
def test_ground_parentless_condition_term_standardizes_nonstandard_source():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))
    query = _find_nonstandard_condition_term(adapter)

    result = service.ground(
        query,
        limit=5,
        domain="Condition",
        vocabulary_id=None,
        parent_ids=None,
    )

    results = result["results"]
    assert results
    assert results[0]["standard_concept"] is True
    assert results[0]["standardized_from"] is not None
    assert result["grounding_explanation"]["parent_ids_source"] == "none"
    assert result["grounding_explanation"]["effective_parent_ids"] == []


@pytest.mark.integration
def test_get_edges_returns_predicate_kinds():
    adapter = _load_graph_adapter()
    graph = GraphService(adapter)

    edges = graph.get_edges(201826)

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
    graph = GraphService(adapter)

    ancestors = graph.get_ancestors(201826, max_depth=1)
    if not ancestors:
        pytest.skip("concept has no ancestors in this dataset")
    parent_id = ancestors[0]["concept_id"]

    result = graph.find_path(201826, parent_id, max_depth=5)

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
    graph = GraphService(adapter)

    result = graph.map_to_standard("ICD10CM", "E11.9")

    assert "source" in result
    assert "standard_concepts" in result
    assert result["source"]["vocabulary_id"] == "ICD10CM"
    assert result["source"]["concept_code"] == "E11.9"
    assert all(c["standard_concept"] is True for c in result["standard_concepts"])
