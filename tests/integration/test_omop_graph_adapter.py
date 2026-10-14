import os

import pytest

from groundworkers.app import build_adapters
from groundworkers.bootstrap import build_app_config
from groundworkers.services.graph import GraphService
from groundworkers.services.grounding import ConceptGroundingService


def _integration_prerequisite_failure(message, error=None):
    if os.getenv("GROUNDWORKERS_REQUIRE_INTEGRATION") == "1":
        if error is not None:
            raise error
        raise AssertionError(message)
    pytest.skip(message)


def _load_graph_adapter():
    try:
        import omop_graph  # noqa: F401
    except ImportError:
        _integration_prerequisite_failure(
            "omop_graph is not installed in this environment",
            RuntimeError("required graph integration needs omop-graph installed"),
        )

    config_path = os.getenv("GROUNDWORKERS_CONFIG_PATH") or os.getenv(
        "GROUNDWORKERS_CONFIG"
    )
    if config_path is None:
        message = (
            "an explicit integration config path is required; set "
            "GROUNDWORKERS_CONFIG_PATH"
        )
        _integration_prerequisite_failure(message, RuntimeError(message))
    try:
        config = build_app_config(config_path=config_path)
    except (FileNotFoundError, ValueError) as exc:
        _integration_prerequisite_failure(
            f"shared stack config is unavailable: {exc}", exc
        )
    adapter = build_adapters(config).omop_graph
    if adapter is None:
        _integration_prerequisite_failure("omop_graph adapter was not built")
    if not adapter.is_available():
        _integration_prerequisite_failure(
            "omop_graph backend is not available in this environment"
        )
    adapter._integration_vocab_engine = config.cdm_vocab_engine
    return adapter


def _require_seeded_concepts(adapter, concept_ids):
    missing = [concept_id for concept_id in concept_ids if adapter.get_concept(concept_id) is None]
    if missing:
        raise AssertionError(f"required OMOP fixture concepts are missing: {missing}")


@pytest.mark.required_integration
def test_required_graph_fixture_concepts_are_seeded():
    adapter = _load_graph_adapter()

    _require_seeded_concepts(adapter, (201826, 201827, 201828, 201837, 201838))


@pytest.mark.required_integration
def test_get_concept_by_known_id():
    adapter = _load_graph_adapter()

    concept = adapter.get_concept(201826)

    assert concept is not None
    assert concept["concept_name"] == "Type 2 diabetes mellitus"


@pytest.mark.required_integration
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
        "classification_concept",
        "valid_start_date",
        "valid_end_date",
        "invalid_reason",
        "is_active",
    }


@pytest.mark.required_integration
def test_ancestors_match_seeded_relationships_and_depths():
    adapter = _load_graph_adapter()
    graph = GraphService(adapter)

    ancestors = graph.get_ancestors(201826, max_depth=5)

    assert [(item["concept_id"], item["depth"]) for item in ancestors] == [(201827, 1)]


@pytest.mark.required_integration
def test_descendants_match_seeded_relationships_and_depths():
    adapter = _load_graph_adapter()
    graph = GraphService(adapter)

    descendants = graph.get_descendants(201827, max_depth=1)

    assert [(item["concept_id"], item["depth"]) for item in descendants] == [
        (201826, 1), (201828, 1), (201829, 1)
    ]


@pytest.mark.required_integration
def test_concept_by_code_snomed():
    adapter = _load_graph_adapter()

    concepts = adapter.get_concept_by_code("SNOMED", "44054006")

    assert concepts
    assert concepts[0]["vocabulary_id"] == "SNOMED"


@pytest.mark.required_integration
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
    assert results[0]["concept_id"] == 201826
    assert results[0]["match_kind"] == "EXACT"
    assert results[0]["concept_name"].lower() == "type 2 diabetes mellitus"
    assert results[0]["standard_concept"] is True


@pytest.mark.required_integration
def test_ground_partial_match():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("type 2 diabet", limit=5, domain=None, vocabulary_id=None)

    results = result["results"]
    assert results
    assert 201826 in {hit["concept_id"] for hit in results}
    assert all(r["standard_concept"] is True for r in results)


@pytest.mark.required_integration
def test_ground_returns_standard_concepts_only():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("diabetes", limit=10, domain=None, vocabulary_id=None)

    results = result["results"]
    assert results
    assert {201826, 201827} & {hit["concept_id"] for hit in results}
    assert all(r["standard_concept"] is True for r in results)
    scores = [r["total_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.required_integration
def test_ground_parentless_condition_term_standardizes_nonstandard_source():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))
    query = "Source diabetes mellitus"

    result = service.ground(
        query,
        limit=5,
        domain="Condition",
        vocabulary_id=None,
        parent_ids=None,
    )

    results = result["results"]
    assert results
    assert results[0]["concept_id"] == 201826
    assert results[0]["standard_concept"] is True
    assert results[0]["standardized_from"] is not None
    assert result["grounding_explanation"]["parent_ids_source"] == "none"
    assert result["grounding_explanation"]["effective_parent_ids"] == []


@pytest.mark.required_integration
def test_standard_flags_distinguish_s_from_c():
    """The strict flag contract distinguishes raw OMOP ``S`` and ``C`` rows."""
    adapter = _load_graph_adapter()

    views = adapter.concept_views([201826, 201834])
    assert views[201826]["standard_concept"] is True
    assert views[201826]["classification_concept"] is False
    assert views[201834]["standard_concept"] is False
    assert views[201834]["classification_concept"] is True


@pytest.mark.required_integration
def test_classification_concept_is_not_standard():
    """A classification concept is never exposed as a standard mapping target."""
    adapter = _load_graph_adapter()

    classification = adapter.get_concept(201834)

    assert classification is not None
    assert classification["classification_concept"] is True
    assert classification["standard_concept"] is False


@pytest.mark.required_integration
def test_grounding_flags_are_mutually_exclusive_across_a_query_set():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))

    for query, domain, expected_id in (
        ("Type 2 diabetes mellitus", None, 201826),
        ("metformin", "Drug", 201830),
        ("appendectomy", "Procedure", 201831),
        ("asthma", None, 201829),
    ):
        result = service.ground(query, limit=10, domain=domain, vocabulary_id=None)
        assert expected_id in {hit["concept_id"] for hit in result["results"]}
        for hit in result["results"]:
            assert not (hit["standard_concept"] and hit["classification_concept"]), (
                f"{query!r} -> {hit['concept_id']} reported as both standard and classification"
            )


@pytest.mark.optional_integration
def test_embedding_tier_produces_candidates_when_configured():
    """Read-only (write=False) grounding must still be able to use the embedding tier.

    omop-graph only encodes queries on demand through a write-capable interface, so
    this fails if Groundworkers stops supplying the query vector itself.
    """
    adapter = _load_graph_adapter()
    if not adapter.embedding_resolver_active:
        pytest.skip("no complete embedding configuration in this environment")
    service = ConceptGroundingService(GraphService(adapter))

    # A paraphrase with no exact, synonym, or full-text match, so only the embedding
    # tier can answer it.
    result = service.ground(
        "sugar diabetes of adulthood", limit=5, domain="Condition", vocabulary_id=None
    )

    explanation = result["grounding_explanation"]
    assert explanation["embedding_tier_detail"] is None
    assert result["results"], "embedding tier returned no candidates"
    assert explanation["matched_tier"] == "EMBEDDING_NEAREST"
    assert explanation["used_embedding"] is True
    assert all(hit["embedding_score"] is not None for hit in result["results"])


@pytest.mark.required_integration
def test_standard_only_and_active_only_exclude_seeded_decoys():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))

    source_query = "Active nonstandard fixture source"
    source_unfiltered = service.ground(
        source_query, limit=20, domain="Condition", vocabulary_id=None
    )
    source_standard = service.ground(
        source_query,
        limit=20,
        domain="Condition",
        vocabulary_id=None,
        standard_only=True,
    )
    assert [hit["concept_id"] for hit in source_unfiltered["results"]] == [201826]
    assert source_unfiltered["results"][0]["standardized_from"]["concept_id"] == 201837
    assert source_standard["results"] == []

    query = "Type 2 diabetes mellitus"
    unfiltered = service.ground(query, limit=20, domain="Condition", vocabulary_id=None)
    standard = service.ground(query, limit=20, domain="Condition", vocabulary_id=None,
                              standard_only=True)
    active = service.ground(query, limit=20, domain="Condition", vocabulary_id=None, active_only=True)
    both = service.ground(query, limit=20, domain="Condition", vocabulary_id=None,
                          standard_only=True, active_only=True)

    def ids(result):
        return {hit["concept_id"] for hit in result["results"]}

    assert ids(unfiltered) == {201826, 201838}
    assert ids(standard) == {201826, 201838}
    assert ids(active) == {201826}
    assert ids(both) == {201826}
    assert standard["grounding_explanation"]["standard_only"] is True
    assert active["grounding_explanation"]["active_only"] is True


@pytest.mark.required_integration
def test_get_edges_match_seeded_relationships():
    adapter = _load_graph_adapter()
    graph = GraphService(adapter)

    edges = graph.get_edges(201826)

    assert "outbound" in edges
    assert "inbound" in edges
    assert [(e["relationship_id"], e["target_concept_id"]) for e in edges["outbound"]] == [
        ("Is a", 201827)
    ]
    assert [(e["relationship_id"], e["source_concept_id"]) for e in edges["inbound"]] == [
        ("Maps to", 201832), ("Maps to", 201833), ("Maps to", 201837)
    ]
    assert edges["outbound"][0]["predicate_kind"] == "HIERARCHY"


@pytest.mark.required_integration
def test_find_path_between_concept_and_its_ancestor():
    adapter = _load_graph_adapter()
    graph = GraphService(adapter)

    result = graph.find_path(201826, 201827, max_depth=5)

    assert result["found"] is True
    assert len(result["paths"]) > 0
    path = result["paths"][0]
    assert path["length"] == len(path["steps"])
    if path["steps"]:
        assert path["steps"][0]["subject_id"] == 201826
        assert path["steps"][-1]["object_id"] == 201827


@pytest.mark.required_integration
def test_map_icd_code_to_standard_snomed():
    adapter = _load_graph_adapter()
    graph = GraphService(adapter)

    result = graph.map_to_standard("ICD10CM", "E11.9")

    assert "source" in result
    assert "standard_concepts" in result
    assert result["source"]["vocabulary_id"] == "ICD10CM"
    assert result["source"]["concept_code"] == "E11.9"
    assert result["source"]["concept_id"] == 201832
    assert [c["concept_id"] for c in result["standard_concepts"]] == [201826]
