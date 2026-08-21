"""R4 — omop-graph 2.x constraint translation and strict concept flags.

Covers the boundary that replaced omop-graph 1.x's deleted concept search
constraint with ``omop_alchemy.cdm.query.ConceptFilter``, the tier plan, and the strict
standard/classification contract that Groundworkers derives from the raw OMOP
flag because omop-graph 2.x only exposes the combined standard-or-classification
boolean.
"""

from __future__ import annotations

from datetime import date

import pytest
from omop_alchemy.cdm.model.vocabulary import Concept
from omop_alchemy.cdm.query import ConceptFilter
from sqlalchemy import create_engine

from groundworkers.adapters.omop_graph import EmbeddingTierUnavailable, OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.services.graph import GraphService
from groundworkers.services.grounding import ConceptGroundingService

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingGraph:
    """Duck-typed GraphService recording the plan it is asked to execute."""

    def __init__(self, *, embedding_resolver_active: bool = False) -> None:
        self.embedding_resolver_active = embedding_resolver_active
        self.plans: list[object] = []

    def canonicalize_domain(self, domain: str | None) -> str | None:
        if domain is None:
            return None
        known = ("Condition", "Procedure", "Drug", "Measurement", "Device", "Observation")
        lowered = domain.lower()
        return next((k for k in known if k.lower() == lowered), domain)

    def ground_with_plan(self, plan) -> dict:
        self.plans.append(plan)
        return {"results": [], "matched_tier": None, "used_embedding": False}


def _constraint_for(**kwargs) -> ConceptFilter | None:
    """Run one ground() call and return the ConceptFilter it built."""
    graph = RecordingGraph()
    service = ConceptGroundingService(graph)
    call = {"limit": 5, "domain": None, "vocabulary_id": None, **kwargs}
    service.ground("diabetes", **call)
    return graph.plans[0].constraints.search_constraint


def _tier_names(graph: RecordingGraph) -> list[tuple[str, ...]]:
    return [
        tuple(type(resolver).__name__ for resolver in tier)
        for tier in graph.plans[0].tiers
    ]


# ---------------------------------------------------------------------------
# Constraint construction
# ---------------------------------------------------------------------------


def test_unconstrained_request_passes_no_filter() -> None:
    # The 0.x code passed no constraint object at all when nothing was set. Keep
    # that: an empty ConceptFilter would still change the resolver code paths.
    assert _constraint_for() is None


def test_domain_only_filter() -> None:
    constraint = _constraint_for(domain="condition")

    assert isinstance(constraint, ConceptFilter)
    assert constraint.domains == ("Condition",)  # canonicalized
    assert constraint.vocabularies is None


def test_vocabulary_only_filter() -> None:
    constraint = _constraint_for(vocabulary_id="SNOMED")

    assert constraint is not None
    assert constraint.domains is None
    assert constraint.vocabularies == ("SNOMED",)


def test_combined_domain_and_vocabulary_filter() -> None:
    constraint = _constraint_for(domain="Condition", vocabulary_id="SNOMED")

    assert constraint is not None
    assert constraint.domains == ("Condition",)
    assert constraint.vocabularies == ("SNOMED",)


def test_default_policy_does_not_require_standard_or_active() -> None:
    # Baseline guard: ConceptFilter added require_standard/require_active, which the
    # removed 1.x constraint type had no equivalent for. Defaulting either to True
    # would silently change which candidates resolve.
    constraint = _constraint_for(domain="Condition")

    assert constraint is not None
    assert constraint.require_standard is False
    assert constraint.require_active is False


def test_standard_only_and_active_only_are_translated_explicitly() -> None:
    constraint = _constraint_for(
        domain="Condition", standard_only=True, active_only=True
    )

    assert constraint is not None
    assert constraint.require_standard is True
    assert constraint.require_active is True


def test_active_policy_alone_still_builds_a_filter() -> None:
    constraint = _constraint_for(active_only=True)

    assert constraint is not None
    assert constraint.domains is None
    assert constraint.vocabularies is None
    assert constraint.require_active is True


def test_candidate_limit_is_never_pushed_into_the_filter() -> None:
    # ConceptFilter.limit becomes the ANN k for the embedding resolver and a SQL
    # LIMIT for the lexical resolvers. Setting it would change candidate recall and
    # ordering; ground_term's max_candidates stays the only cap.
    for kwargs in ({}, {"domain": "Condition"}, {"active_only": True}):
        constraint = _constraint_for(**kwargs)
        if constraint is not None:
            assert constraint.limit is None


def test_result_limit_is_still_passed_to_the_plan() -> None:
    graph = RecordingGraph()
    ConceptGroundingService(graph).ground(
        "diabetes", limit=7, domain=None, vocabulary_id=None
    )

    assert graph.plans[0].limit == 7


@pytest.mark.parametrize("limit", [0, -1, -10])
def test_non_positive_limit_is_rejected_before_constructing_a_filter(limit: int) -> None:
    graph = RecordingGraph()
    service = ConceptGroundingService(graph)

    with pytest.raises(GroundworkersError) as excinfo:
        service.ground("diabetes", limit=limit, domain="Condition", vocabulary_id=None)

    assert excinfo.value.code == "INVALID_INPUT"
    assert str(limit) in excinfo.value.message
    # Rejected at the Groundworkers boundary, not by another package's dataclass.
    assert graph.plans == []


def test_concept_filter_itself_rejects_a_non_positive_limit() -> None:
    # Contract check on the upstream type the guard above protects against.
    with pytest.raises(ValueError):
        ConceptFilter(limit=0)


def test_empty_parent_ids_is_rejected() -> None:
    graph = RecordingGraph()

    with pytest.raises(GroundworkersError) as excinfo:
        ConceptGroundingService(graph).ground(
            "diabetes", limit=5, domain=None, vocabulary_id=None, parent_ids=()
        )

    assert excinfo.value.code == "QUERY_ERROR"


def test_blank_query_is_rejected() -> None:
    with pytest.raises(ValueError):
        ConceptGroundingService(RecordingGraph()).ground(
            "   ", limit=5, domain=None, vocabulary_id=None
        )


# ---------------------------------------------------------------------------
# Tier plan
# ---------------------------------------------------------------------------


def test_tier_plan_is_lexical_only_when_embedding_is_unavailable() -> None:
    graph = RecordingGraph(embedding_resolver_active=False)
    ConceptGroundingService(graph).ground(
        "diabetes", limit=5, domain="Condition", vocabulary_id=None
    )

    assert _tier_names(graph) == [
        ("ExactLabelResolver", "ExactSynonymResolver"),
        ("FullTextResolver", "FullTextSynonymResolver"),
        ("PartialLabelResolver", "PartialSynonymResolver"),
    ]


def test_tier_plan_adds_embedding_tier_before_partial_when_active() -> None:
    graph = RecordingGraph(embedding_resolver_active=True)
    ConceptGroundingService(graph).ground(
        "diabetes", limit=5, domain="Condition", vocabulary_id=None
    )

    assert _tier_names(graph) == [
        ("ExactLabelResolver", "ExactSynonymResolver"),
        ("FullTextResolver", "FullTextSynonymResolver"),
        ("EmbeddingResolver",),
        ("PartialLabelResolver", "PartialSynonymResolver"),
    ]


def test_partial_tier_is_withheld_when_the_search_space_is_not_narrowed() -> None:
    graph = RecordingGraph()
    ConceptGroundingService(graph).ground(
        "diabetes", limit=5, domain=None, vocabulary_id=None
    )

    assert ("PartialLabelResolver", "PartialSynonymResolver") not in _tier_names(graph)


def test_standard_only_does_not_count_as_narrowing_for_the_partial_tier() -> None:
    # require_standard/require_active are non-selective over the concept table, so
    # they must not unlock a full-table ILIKE scan the way a domain filter does.
    graph = RecordingGraph()
    ConceptGroundingService(graph).ground(
        "diabetes",
        limit=5,
        domain=None,
        vocabulary_id=None,
        standard_only=True,
        active_only=True,
    )

    assert ("PartialLabelResolver", "PartialSynonymResolver") not in _tier_names(graph)


def test_partial_tier_is_withheld_for_long_queries() -> None:
    graph = RecordingGraph()
    ConceptGroundingService(graph).ground(
        "x" * 61, limit=5, domain="Condition", vocabulary_id=None
    )

    assert ("PartialLabelResolver", "PartialSynonymResolver") not in _tier_names(graph)


def test_explanation_reports_the_applied_policy() -> None:
    graph = RecordingGraph()
    result = ConceptGroundingService(graph).ground(
        "diabetes",
        limit=5,
        domain=None,
        vocabulary_id=None,
        standard_only=True,
        active_only=False,
    )

    explanation = result["grounding_explanation"]
    assert explanation["standard_only"] is True
    assert explanation["active_only"] is False


# ---------------------------------------------------------------------------
# Strict standard / classification flags on grounding results
# ---------------------------------------------------------------------------


class StubGroundAdapter:
    """Adapter double returning one grounded hit with a controllable raw flag."""

    def __init__(self, raw_flag: str | None, *, is_active: bool = True) -> None:
        self._raw_flag = raw_flag
        self._is_active = is_active
        self.embedding_resolver_active = False

    def canonicalize_domain(self, domain: str | None) -> str | None:
        return domain

    def run_ground_tier(self, _tier, _query, *, constraints, limit) -> list[dict]:
        return [
            {
                "concept_id": 42,
                "concept_name": "Demo concept",
                "match_kind": "EXACT",
                "matched_label": "demo",
                "total_score": 1.0,
                "relevance": 1.0,
                "parsimony_penalty": 0.0,
                "broadness_bonus": 0.0,
                "embedding_score": None,
                "separation": 0,
                "standardized_from": None,
            }
        ]

    def concept_views(self, concept_ids) -> dict[int, dict]:
        return {
            42: {
                "concept_id": 42,
                "concept_name": "Demo concept",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                # omop-graph's combined standard-or-classification boolean: true for
                # both 'S' and 'C'. Deliberately true here so the strict flags below
                # cannot be passing by accidentally reading this field.
                "standard_concept": self._raw_flag in {"S", "C"},
                "is_active": self._is_active,
            }
        }

    def raw_standard_flags(self, concept_ids) -> dict[int, str | None]:
        return {42: self._raw_flag}


@pytest.mark.parametrize(
    ("raw_flag", "standard", "classification"),
    [
        ("S", True, False),
        ("C", False, True),
        (None, False, False),
    ],
)
def test_grounding_results_report_strict_flags(
    raw_flag: str | None, standard: bool, classification: bool
) -> None:
    adapter = StubGroundAdapter(raw_flag)
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("demo", limit=5, domain=None, vocabulary_id=None)

    hit = result["results"][0]
    assert hit["standard_concept"] is standard
    assert hit["classification_concept"] is classification


def test_classification_result_is_returned_not_dropped() -> None:
    # Labelling, not filtering: a classification concept is a real grounding answer
    # and callers decide whether it is an acceptable mapping target.
    adapter = StubGroundAdapter("C")
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("demo", limit=5, domain=None, vocabulary_id=None)

    assert [hit["concept_id"] for hit in result["results"]] == [42]


def test_grounding_result_carries_the_graph_activity_field() -> None:
    adapter = StubGroundAdapter("S", is_active=False)
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("demo", limit=5, domain=None, vocabulary_id=None)

    assert result["results"][0]["is_active"] is False


def test_raw_flags_are_requested_only_for_returned_concepts() -> None:
    class Recording(StubGroundAdapter):
        def __init__(self) -> None:
            super().__init__("S")
            self.requested: list[tuple[int, ...]] = []

        def raw_standard_flags(self, concept_ids):
            self.requested.append(tuple(concept_ids))
            return super().raw_standard_flags(concept_ids)

    adapter = Recording()
    ConceptGroundingService(GraphService(adapter)).ground(
        "demo", limit=5, domain=None, vocabulary_id=None
    )

    assert adapter.requested == [(42,)]


# ---------------------------------------------------------------------------
# Embedding tier availability and fallback
# ---------------------------------------------------------------------------


def test_embedding_tier_requires_a_query_encoder() -> None:
    """A store plus a model is not enough to run the embedding tier.

    The read-oriented graph is built with ``write=False``, and omop-graph only
    encodes queries on demand through a write-capable interface. Without a
    Groundworkers-supplied encoder the resolver would run and silently match
    nothing, so the tier must report itself unavailable instead.
    """
    engine = create_engine("sqlite:///:memory:")
    configured = {
        "embedding_backend_factory": lambda: object(),
        "resolved_embedding_model": object(),
    }

    without_encoder = OmopGraphAdapter(engine, **configured)  # type: ignore[arg-type]
    with_encoder = OmopGraphAdapter(
        engine, **configured, model_backend_factory=lambda: object()  # type: ignore[arg-type]
    )

    # Simulate a graph that accepted the store/model configuration, so the encoder is
    # the only remaining difference between the two adapters.
    without_encoder._embedding_configured = True
    with_encoder._embedding_configured = True

    assert without_encoder.embedding_resolver_active is False
    assert with_encoder.embedding_resolver_active is True


def test_encode_query_without_a_model_backend_reports_tier_unavailable() -> None:
    adapter = OmopGraphAdapter(create_engine("sqlite:///:memory:"))

    with pytest.raises(EmbeddingTierUnavailable) as excinfo:
        adapter._encode_query("diabetes")

    assert "embedding_model_name" in excinfo.value.message


def test_encode_query_failure_detail_hides_provider_errors(monkeypatch) -> None:
    def exploding_backend():
        raise RuntimeError("ollama at http://localhost:11434 rejected api_key=sk-abc123")

    adapter = OmopGraphAdapter(
        create_engine("sqlite:///:memory:"),
        model_backend_factory=exploding_backend,  # type: ignore[arg-type]
    )

    with pytest.raises(EmbeddingTierUnavailable) as excinfo:
        adapter._encode_query("diabetes")

    detail = excinfo.value.message
    assert "sk-abc123" not in detail
    assert "localhost:11434" not in detail
    assert "RuntimeError" in detail
    assert "lexical tiers remain available" in detail


class TierSkippingAdapter(StubGroundAdapter):
    """Fails the embedding tier and succeeds on a later lexical tier."""

    def __init__(self) -> None:
        super().__init__("S")
        self.embedding_resolver_active = True
        self.tiers_attempted: list[str] = []

    def run_ground_tier(self, tier, query, *, constraints, limit):
        names = "+".join(type(resolver).__name__ for resolver in tier)
        self.tiers_attempted.append(names)
        if "EmbeddingResolver" in names:
            raise EmbeddingTierUnavailable("encoder offline; lexical tiers remain available")
        if "PartialLabelResolver" in names:
            return super().run_ground_tier(tier, query, constraints=constraints, limit=limit)
        return []


def test_unavailable_embedding_tier_falls_through_to_lexical_tiers() -> None:
    adapter = TierSkippingAdapter()
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("demo", limit=5, domain="Condition", vocabulary_id=None)

    # The embedding tier was attempted, skipped, and the partial tier still answered.
    assert any("EmbeddingResolver" in name for name in adapter.tiers_attempted)
    assert [hit["concept_id"] for hit in result["results"]] == [42]
    assert result["grounding_explanation"]["matched_tier"] == "EXACT"


def test_skipped_embedding_tier_is_reported_not_silent() -> None:
    adapter = TierSkippingAdapter()
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("demo", limit=5, domain="Condition", vocabulary_id=None)

    detail = result["grounding_explanation"]["embedding_tier_detail"]
    assert detail is not None
    assert "lexical tiers remain available" in detail


def test_healthy_grounding_reports_no_embedding_tier_detail() -> None:
    adapter = StubGroundAdapter("S")
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground("demo", limit=5, domain=None, vocabulary_id=None)

    assert result["grounding_explanation"]["embedding_tier_detail"] is None


# ---------------------------------------------------------------------------
# Raw-flag query regression fixtures (real SQLite CDM)
# ---------------------------------------------------------------------------

# Fixed concept rows covering every standard_concept / invalid_reason shape the
# public contract has to distinguish. Blank and whitespace-only values are legal
# in real vocabularies and must normalize to "unset", not to a truthy flag.
RAW_FLAG_FIXTURES: tuple[tuple[int, str, str | None, str | None], ...] = (
    (1, "standard concept", "S", None),
    (2, "classification concept", "C", None),
    (3, "non standard concept", None, None),
    (4, "blank flag concept", "", None),
    (5, "whitespace flag concept", " ", None),
    (6, "deprecated standard concept", "S", "D"),
    (7, "upgraded standard concept", "S", "U"),
    (8, "blank invalid reason concept", "S", ""),
    (9, "whitespace invalid reason concept", "S", " "),
)


@pytest.fixture()
def sqlite_cdm_adapter():
    """An OmopGraphAdapter over a minimal real SQLite concept table."""
    engine = create_engine("sqlite:///:memory:")
    Concept.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            Concept.__table__.insert(),
            [
                {
                    "concept_id": concept_id,
                    "concept_name": name,
                    "domain_id": "Condition",
                    "vocabulary_id": "SNOMED",
                    "concept_class_id": "Clinical Finding",
                    "standard_concept": standard_concept,
                    "concept_code": str(concept_id),
                    "valid_start_date": date(1970, 1, 1),
                    "valid_end_date": date(2099, 12, 31),
                    "invalid_reason": invalid_reason,
                }
                for concept_id, name, standard_concept, invalid_reason in RAW_FLAG_FIXTURES
            ],
        )
    return OmopGraphAdapter(engine)


@pytest.mark.parametrize(
    ("concept_id", "expected_flag"),
    [
        (1, "S"),
        (2, "C"),
        (3, None),
        (4, None),  # blank normalizes to unset
        (5, None),  # whitespace-only normalizes to unset
    ],
)
def test_raw_standard_flags_reads_the_unnormalized_omop_flag(
    sqlite_cdm_adapter, concept_id: int, expected_flag: str | None
) -> None:
    flags = sqlite_cdm_adapter.raw_standard_flags([concept_id])

    assert flags == {concept_id: expected_flag}


def test_raw_standard_flags_distinguishes_standard_from_classification(
    sqlite_cdm_adapter,
) -> None:
    # The whole point of the local query: omop-graph's ConceptView.standard_concept
    # is true for both of these, so it cannot carry the public contract.
    flags = sqlite_cdm_adapter.raw_standard_flags([1, 2])

    assert flags[1] == "S"
    assert flags[2] == "C"


def test_raw_standard_flags_batches_and_deduplicates(sqlite_cdm_adapter) -> None:
    flags = sqlite_cdm_adapter.raw_standard_flags([1, 2, 3, 1, 2])

    assert flags == {1: "S", 2: "C", 3: None}


def test_raw_standard_flags_omits_unknown_concepts(sqlite_cdm_adapter) -> None:
    flags = sqlite_cdm_adapter.raw_standard_flags([1, 999_999])

    assert flags == {1: "S"}


def test_raw_standard_flags_short_circuits_on_empty_input() -> None:
    # No engine access at all, so callers can pass an empty result set safely.
    adapter = OmopGraphAdapter(create_engine("sqlite:///:memory:"))

    assert adapter.raw_standard_flags([]) == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [("S", "S"), ("C", "C"), (None, None), ("", None), ("   ", None), (" S ", "S")],
)
def test_raw_flag_normalization(value: str | None, expected: str | None) -> None:
    assert OmopGraphAdapter._normalise_raw_flag(value) == expected


def test_invalid_reason_normalization_matches_the_activity_contract() -> None:
    # Validity uses the same normalization: 'D'/'U' are inactive; NULL, blank and
    # whitespace-only are active.
    normalise = OmopGraphAdapter._normalise_raw_flag

    assert normalise("D") == "D"
    assert normalise("U") == "U"
    assert normalise(None) is None
    assert normalise("") is None
    assert normalise("  ") is None
