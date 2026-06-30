from __future__ import annotations

from groundworkers.services.grounding import GroundingService


class StubGraphAdapter:
    def __init__(self, *, embedding_resolver_active: bool = False) -> None:
        self.embedding_resolver_active = embedding_resolver_active
        self.calls: list[tuple[str, object]] = []

    def canonicalize_domain(self, domain: str | None) -> str | None:
        self.calls.append(("canonicalize_domain", domain))
        if domain == "condition":
            return "Condition"
        return domain

    def get_domain_root_ids(self, domain: str | None) -> tuple[int, ...]:
        self.calls.append(("get_domain_root_ids", domain))
        if domain == "Condition":
            return (404684003,)
        if domain == "Observation":
            return (27,)
        return ()

    def known_grounding_domains(self) -> tuple[str, ...]:
        return ("Condition", "Observation")

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


def test_grounding_service_builds_plan_with_explicit_parent_ids() -> None:
    adapter = StubGraphAdapter(embedding_resolver_active=False)
    service = GroundingService(adapter, min_fulltext_overlap=0.25)

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
    }


def test_grounding_service_includes_embedding_tier_when_adapter_is_embedding_active() -> None:
    adapter = StubGraphAdapter(embedding_resolver_active=True)
    service = GroundingService(adapter)

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


def test_grounding_service_without_parent_ids_uses_domain_root_fallback() -> None:
    adapter = StubGraphAdapter()
    service = GroundingService(adapter)

    result = service.ground(
        "diabetes",
        limit=5,
        domain="Condition",
        vocabulary_id=None,
        parent_ids=None,
    )

    request = _request_from_calls(adapter)

    assert request.constraints.parent_ids == (404684003,)
    assert result["grounding_explanation"]["effective_parent_ids"] == [404684003]
    assert result["grounding_explanation"]["parent_ids_source"] == "domain_root"


def test_grounding_service_without_parent_ids_or_domain_aggregates_known_roots() -> None:
    adapter = StubGraphAdapter()
    service = GroundingService(adapter)

    result = service.ground(
        "observation text",
        limit=5,
        domain=None,
        vocabulary_id=None,
        parent_ids=None,
    )

    request = _request_from_calls(adapter)

    assert request.constraints.parent_ids == (404684003, 27)
    assert result["grounding_explanation"]["effective_parent_ids"] == [404684003, 27]
    assert result["grounding_explanation"]["parent_ids_source"] == "all_domain_roots"
