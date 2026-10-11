import os

import pytest
from oa_configurator import Role, physical_schema_of
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Relationship
from sqlalchemy import exists, func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import aliased

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
    if config_path is None:
        message = (
            "an explicit integration config path is required; set "
            "GROUNDWORKERS_CONFIG_PATH"
        )
        if os.getenv("GROUNDWORKERS_REQUIRE_INTEGRATION") == "1":
            raise RuntimeError(message)
        pytest.skip(message)
    try:
        config = build_app_config(config_path=config_path)
    except (FileNotFoundError, ValueError) as exc:
        if os.getenv("GROUNDWORKERS_REQUIRE_INTEGRATION") == "1":
            raise
        pytest.skip(f"shared stack config is unavailable: {exc}")
    adapter = build_adapters(config).omop_graph
    if adapter is None:
        pytest.skip("omop_graph adapter was not built")
    if not adapter.is_available():
        pytest.skip("omop_graph backend is not available in this environment")
    adapter._integration_vocab_engine = config.cdm_vocab_engine
    return adapter


def _find_nonstandard_condition_term(adapter) -> str:
    standard = aliased(Concept)
    same_name = aliased(Concept)
    stmt = (
        select(Concept.concept_name)
        .join(
            Concept_Relationship,
            (Concept_Relationship.concept_id_1 == Concept.concept_id)
            & (func.lower(Concept_Relationship.relationship_id) == "maps to")
            & Concept_Relationship.invalid_reason.is_(None),
        )
        .join(
            standard,
            (standard.concept_id == Concept_Relationship.concept_id_2)
            & (standard.standard_concept == "S")
            & (standard.domain_id == "Condition")
            & standard.invalid_reason.is_(None),
        )
        .where(
            Concept.domain_id == "Condition",
            Concept.standard_concept.is_(None),
            Concept.invalid_reason.is_(None),
            ~exists(
                select(1).where(
                    func.lower(same_name.concept_name)
                    == func.lower(Concept.concept_name),
                    same_name.domain_id == "Condition",
                    same_name.standard_concept.in_(("S", "C")),
                    same_name.invalid_reason.is_(None),
                )
            ),
        )
        .order_by(Concept.concept_id)
        .limit(1)
    )
    with adapter._cdm_session_factory() as session:
        row = session.execute(stmt).first()
    if row is None:
        pytest.skip(
            "no uniquely named non-standard Condition concept with a standard mapping was found"
        )
    return str(row[0])


def _skip_if_parent_lookup_is_unindexed(adapter) -> None:
    """Skip hierarchy smoke checks when the live vocabulary DB is not shaped for them."""

    try:
        schema = physical_schema_of(
            adapter._integration_vocab_engine, schema_tag=Role.VOCAB
        )
        indexes = sa_inspect(adapter._integration_vocab_engine).get_indexes(
            "concept_ancestor", schema=schema
        )
    except Exception as exc:
        # Broad except: integration environment preflight.
        pytest.skip(f"could not inspect concept_ancestor indexes: {exc}")

    if not any(
        "descendant_concept_id" in (index.get("column_names") or [])
        or any(
            "descendant_concept_id" in str(expression).lower()
            for expression in (index.get("expressions") or ())
        )
        for index in indexes
    ):
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
        "classification_concept",
        "valid_start_date",
        "valid_end_date",
        "invalid_reason",
        "is_active",
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
def test_standard_flags_distinguish_s_from_c():
    """The strict flag contract distinguishes raw OMOP ``S`` and ``C`` rows."""
    adapter = _load_graph_adapter()

    stmt = (
        select(Concept.standard_concept, func.min(Concept.concept_id))
        .where(Concept.standard_concept.in_(("S", "C")))
        .group_by(Concept.standard_concept)
    )
    with adapter._cdm_session_factory() as session:
        sampled = {row[0]: int(row[1]) for row in session.execute(stmt).all()}
    if set(sampled) != {"S", "C"}:
        pytest.skip("vocabulary does not contain both 'S' and 'C' concepts")

    views = adapter.concept_views(list(sampled.values()))
    assert views[sampled["S"]]["standard_concept"] is True
    assert views[sampled["S"]]["classification_concept"] is False
    assert views[sampled["C"]]["standard_concept"] is False
    assert views[sampled["C"]]["classification_concept"] is True


@pytest.mark.integration
def test_classification_concept_is_not_standard():
    """A classification concept is never exposed as a standard mapping target."""
    adapter = _load_graph_adapter()

    other = aliased(Concept)
    stmt = (
        select(Concept.concept_name)
        .where(
            Concept.standard_concept == "C",
            Concept.domain_id == "Drug",
            Concept.invalid_reason.is_(None),
            ~exists(
                select(1).where(
                    func.lower(other.concept_name) == func.lower(Concept.concept_name),
                    other.standard_concept == "S",
                )
            ),
        )
        .order_by(Concept.concept_id)
        .limit(1)
    )
    with adapter._cdm_session_factory() as session:
        row = session.execute(stmt).first()
    if row is None:
        pytest.skip("no uniquely named classification Drug concept was found")

    with adapter._cdm_session_factory() as session:
        concept_id = session.execute(
            select(Concept.concept_id).where(Concept.concept_name == row[0])
        ).scalar_one()
    classification = adapter.get_concept(int(concept_id))

    assert classification is not None
    assert classification["classification_concept"] is True
    assert classification["standard_concept"] is False


@pytest.mark.integration
def test_grounding_flags_are_mutually_exclusive_across_a_query_set():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))

    for query, domain in (
        ("Type 2 diabetes mellitus", None),
        ("metformin", "Drug"),
        ("appendectomy", "Procedure"),
        ("asthma", None),
    ):
        result = service.ground(query, limit=10, domain=domain, vocabulary_id=None)
        for hit in result["results"]:
            assert not (hit["standard_concept"] and hit["classification_concept"]), (
                f"{query!r} -> {hit['concept_id']} reported as both standard and classification"
            )


@pytest.mark.integration
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


@pytest.mark.integration
def test_standard_only_and_active_only_narrow_candidate_resolution():
    adapter = _load_graph_adapter()
    service = ConceptGroundingService(GraphService(adapter))

    unfiltered = service.ground(
        "diabetes", limit=20, domain="Condition", vocabulary_id=None
    )
    filtered = service.ground(
        "diabetes",
        limit=20,
        domain="Condition",
        vocabulary_id=None,
        standard_only=True,
        active_only=True,
    )

    assert filtered["grounding_explanation"]["standard_only"] is True
    assert filtered["grounding_explanation"]["active_only"] is True
    # Filtering can only narrow the candidate pool, never widen it.
    assert len(filtered["results"]) <= len(unfiltered["results"])


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
