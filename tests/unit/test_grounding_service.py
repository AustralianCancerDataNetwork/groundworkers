from __future__ import annotations

from groundworkers.services.graph import GraphService
from groundworkers.services.grounding import ConceptGroundingService


class StubOmopGraphAdapter:
    """Minimal stand-in for OmopGraphAdapter, exposing only the members that
    GraphService delegates to. Used to exercise the real GraphService façade."""

    def __init__(
        self,
        *,
        embedding_resolver_active: bool = False,
    ) -> None:
        self._embedding_resolver_active = embedding_resolver_active
        self.calls: list[tuple[str, object]] = []

    def canonicalize_domain(self, domain: str | None) -> str | None:
        self.calls.append(("canonicalize_domain", domain))
        return "Condition" if domain == "condition" else domain

    @property
    def embedding_resolver_active(self) -> bool:
        return self._embedding_resolver_active

    def run_ground_tier(self, _tier, _query, *, constraints, limit):
        self.calls.append(("run_ground_tier", {"constraints": constraints, "limit": limit}))
        return [
            {
                "concept_id": 201826,
                "concept_name": "Type 2 diabetes mellitus",
                "match_kind": "EXACT",
                "matched_label": "diabetes",
                "total_score": 1.0,
                "relevance": 1.0,
                "parsimony_penalty": 0.0,
                "broadness_bonus": 0.0,
                "embedding_score": 0.95 if self._embedding_resolver_active else None,
                "separation": 0,
                "standardized_from": None,
            }
        ]

    def concept_views(self, concept_ids):
        self.calls.append(("concept_views", tuple(concept_ids)))
        return {
            201826: {
                "concept_id": 201826,
                "concept_name": "Type 2 diabetes mellitus",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                # omop-graph >= 2.1 reports the two flags separately.
                "standard_concept": True,
                "classification_concept": False,
                "is_active": True,
            }
        }


class StubGraphAdapter:
    def __init__(self, *, embedding_resolver_active: bool = False) -> None:
        self.embedding_resolver_active = embedding_resolver_active
        self.calls: list[tuple[str, object]] = []

    def canonicalize_domain(self, domain: str | None) -> str | None:
        self.calls.append(("canonicalize_domain", domain))
        if domain == "condition":
            return "Condition"
        return domain

    def ground_with_plan(self, request) -> dict:
        self.calls.append(("ground_with_plan", request))
        return {
            "results": [{"concept_id": 201826, "match_kind": "EXACT"}],
            "matched_tier": "EXACT",
            "used_embedding": self.embedding_resolver_active,
        }


def _request_from_calls(adapter: StubGraphAdapter):
    for name, payload in adapter.calls:
        if name == "ground_with_plan":
            return payload
    raise AssertionError("ground_with_plan was not called")


def _run_ground_tier_call(adapter: StubOmopGraphAdapter):
    for name, payload in adapter.calls:
        if name == "run_ground_tier":
            return payload
    raise AssertionError("run_ground_tier was not called")


def test_grounding_service_builds_plan_with_explicit_parent_ids() -> None:
    adapter = StubGraphAdapter(embedding_resolver_active=False)
    service = ConceptGroundingService(adapter, min_fulltext_overlap=0.25)

    result = service.ground(
        "diabetes",
        limit=5,
        domain="condition",
        vocabulary_id="SNOMED",
        parent_ids=(4002649,),
    )

    request = _request_from_calls(adapter)

    assert request.query == "diabetes"
    assert request.limit == 5
    assert request.constraints.parent_ids == (4002649,)
    assert request.constraints.search_constraint.domains == ("Condition",)
    assert request.constraints.search_constraint.vocabularies == ("SNOMED",)
    assert request.min_fulltext_overlap == 0.25
    assert [tuple(type(resolver).__name__ for resolver in tier) for tier in request.tiers] == [
        ("ExactLabelResolver", "ExactSynonymResolver"),
        ("FullTextResolver", "FullTextSynonymResolver"),
        ("PartialLabelResolver", "PartialSynonymResolver"),
    ]
    assert result["grounding_explanation"] == {
        "matched_tier": "EXACT",
        "used_embedding": False,
        "effective_parent_ids": [4002649],
        "parent_ids_source": "explicit",
        "standard_only": False,
        "active_only": False,
        "embedding_tier_detail": None,
    }


def test_grounding_service_includes_embedding_tier_when_adapter_is_embedding_active() -> None:
    adapter = StubGraphAdapter(embedding_resolver_active=True)
    service = ConceptGroundingService(adapter)

    service.ground(
        "diabetes",
        limit=5,
        domain="Condition",
        vocabulary_id=None,
        parent_ids=(4002649,),
    )

    request = _request_from_calls(adapter)

    assert [tuple(type(resolver).__name__ for resolver in tier) for tier in request.tiers] == [
        ("ExactLabelResolver", "ExactSynonymResolver"),
        ("FullTextResolver", "FullTextSynonymResolver"),
        ("EmbeddingResolver",),
        ("PartialLabelResolver", "PartialSynonymResolver"),
    ]


def test_grounding_service_without_parent_ids_uses_parentless_grounding() -> None:
    adapter = StubGraphAdapter()
    service = ConceptGroundingService(adapter)

    result = service.ground(
        "diabetes",
        limit=5,
        domain="Condition",
        vocabulary_id=None,
        parent_ids=None,
    )

    request = _request_from_calls(adapter)

    assert request.constraints.parent_ids is None
    assert result["grounding_explanation"]["effective_parent_ids"] == []
    assert result["grounding_explanation"]["parent_ids_source"] == "none"


def test_grounding_service_works_through_real_graph_service_facade() -> None:
    # Regression guard for the adapter -> GraphService seam. app.build_services wires
    # ConceptGroundingService around a *real* GraphService, so GraphService must expose
    # canonicalize_domain / embedding_resolver_active / ground_with_plan by delegating
    # to the adapter. The other tests inject a duck-typed stub as `graph` directly,
    # which silently masks a missing delegation (the real cause of the grounding outage
    # found in review). This test fails with AttributeError if any delegation is dropped.
    adapter = StubOmopGraphAdapter(embedding_resolver_active=True)
    service = ConceptGroundingService(GraphService(adapter))

    result = service.ground(
        "diabetes",
        limit=5,
        domain="condition",
        vocabulary_id="SNOMED",
        parent_ids=(4002649,),
    )

    # domain canonicalization was delegated through the façade to the adapter
    assert ("canonicalize_domain", "condition") in adapter.calls
    call = _run_ground_tier_call(adapter)
    assert call["constraints"].search_constraint.domains == ("Condition",)
    # the embedding tier was selected via the delegated embedding_resolver_active property
    assert result["grounding_explanation"]["used_embedding"] is True
    assert result["results"][0]["concept_id"] == 201826
    assert result["results"][0]["match_kind"] == "EXACT"
