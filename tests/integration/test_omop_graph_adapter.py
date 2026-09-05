import os

import pytest
from sqlalchemy import text

from groundworkers.app import build_adapters
from groundworkers.bootstrap import build_app_config
from groundworkers.services.graph import GraphService
from groundworkers.services.grounding import ConceptGroundingService


def _vocab_schema(adapter) -> str:
    """Return the schema selected by the resolved SQLAlchemy engine."""

    schema_map = adapter.engine.get_execution_options().get("schema_translate_map", {})
    return str(
        schema_map.get(None)
        or adapter.engine.dialect.default_schema_name
        or "public"
    )


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
    return adapter


def _find_nonstandard_condition_term(adapter) -> str:
    schema = _vocab_schema(adapter)
    stmt = text(
        f"""
        SELECT c.concept_name
        FROM {schema}.concept AS c
        JOIN {schema}.concept_relationship AS cr
          ON cr.concept_id_1 = c.concept_id
         AND lower(cr.relationship_id) = 'maps to'
         AND cr.invalid_reason IS NULL
        JOIN {schema}.concept AS s
          ON s.concept_id = cr.concept_id_2
         AND s.standard_concept = 'S'
         AND s.domain_id = 'Condition'
         AND s.invalid_reason IS NULL
        WHERE c.domain_id = 'Condition'
          AND c.standard_concept IS NULL
          AND c.invalid_reason IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM {schema}.concept AS c2
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

    schema = _vocab_schema(adapter)
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
            rows = conn.execute(stmt, {"schema": schema}).scalars().all()
    except Exception as exc:
        # Broad except: integration environment preflight.
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

    schema = _vocab_schema(adapter)
    stmt = text(
        f"""
        SELECT standard_concept, MIN(concept_id) AS concept_id
        FROM {schema}.concept
        WHERE standard_concept IN ('S', 'C')
        GROUP BY standard_concept
        """
    )
    with adapter.engine.connect() as conn:
        sampled = {row[0]: int(row[1]) for row in conn.execute(stmt).all()}
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

    schema = _vocab_schema(adapter)
    stmt = text(
        f"""
        SELECT concept_name
        FROM {schema}.concept
        WHERE standard_concept = 'C'
          AND domain_id = 'Drug'
          AND invalid_reason IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM {schema}.concept AS other
              WHERE lower(other.concept_name) = lower(concept.concept_name)
                AND other.standard_concept = 'S'
          )
        ORDER BY concept_id
        LIMIT 1
        """
    )
    with adapter.engine.connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        pytest.skip("no uniquely named classification Drug concept was found")

    with adapter.engine.connect() as conn:
        concept_id = conn.execute(
            text(f"SELECT concept_id FROM {schema}.concept WHERE concept_name = :name"),
            {"name": row[0]},
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
