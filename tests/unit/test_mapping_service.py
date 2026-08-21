

import pytest

from groundworkers.services import MappingService
from groundworkers.services.vocab import (
    ConceptMatch,
    MappedConcept,
    RelatedConceptMapping,
    StandardMapping,
)


class StubVocabAdapter:
    def search_exact(self, query: str, *, domain=None, vocabulary_id=None, standard_only=False, active_only=False, include_synonyms=True, parent_ids=None, limit=20):
        return [
            ConceptMatch(
                concept_id=101,
                concept_name="Diabetes mellitus",
                concept_code="44054006",
                vocabulary_id="SNOMED",
                domain_id="Condition",
                concept_class_id="Clinical Finding",
                standard_concept=True,
                invalid_reason=None,
                match_source="name",
            )
        ]

    def search_normalized(self, query: str, *, domain=None, vocabulary_id=None, standard_only=False, active_only=False, include_synonyms=False, normalization_profile="verbatim", parent_ids=None, remove_stop_phrases=True, limit=20):
        return [
            ConceptMatch(
                concept_id=102,
                concept_name="Type 2 diabetes mellitus",
                concept_code="44054007",
                vocabulary_id="SNOMED",
                domain_id="Condition",
                concept_class_id="Clinical Finding",
                standard_concept=False,
                invalid_reason=None,
                match_source="name",
            )
        ]

    def search_fulltext(self, query: str, *, domain=None, vocabulary_id=None, standard_only=False, active_only=False, include_synonyms=True, parent_ids=None, min_rank=0.0, limit=20):
        return ([
            ConceptMatch(
                concept_id=103,
                concept_name="Diabetic disorder",
                concept_code="44054008",
                vocabulary_id="SNOMED",
                domain_id="Condition",
                concept_class_id="Clinical Finding",
                standard_concept=False,
                invalid_reason=None,
                match_source="name",
                ts_rank=0.42,
            )
        ], True)

    def navigate_to_standard(self, concept_ids: list[int]):
        return [
            StandardMapping(
                source_concept_id=102,
                source_concept_name="Type 2 diabetes mellitus",
                source_standard_concept=False,
                standard_concepts=[
                    MappedConcept(
                        concept_id=201,
                        concept_name="Type 2 diabetes mellitus (standard)",
                        vocabulary_id="SNOMED",
                        domain_id="Condition",
                        concept_class_id="Clinical Finding",
                        relationship_id="Maps to",
                    )
                ],
            )
        ]

    def navigate_to_value(self, concept_ids: list[int]):
        return [
            RelatedConceptMapping(
                source_concept_id=555,
                source_concept_name="Source concept",
                source_standard_concept=False,
                related_concepts=[
                    MappedConcept(
                        concept_id=777,
                        concept_name="Positive",
                        vocabulary_id="SNOMED",
                        domain_id="Observation",
                        concept_class_id="Qualifier Value",
                        relationship_id="Maps to value",
                    )
                ],
            )
        ]


class StubGraphAdapter:
    def get_concept(self, concept_id: int):
        concepts = {
            102: {
                "concept_id": 102,
                "concept_name": "Type 2 diabetes mellitus",
                "concept_code": "44054007",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                "standard_concept": False,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            },
            201: {
                "concept_id": 201,
                "concept_name": "Type 2 diabetes mellitus (standard)",
                "concept_code": "201",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                "standard_concept": True,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            },
            301: {
                "concept_id": 301,
                "concept_name": "Diabetes mellitus",
                "concept_code": "301",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                "standard_concept": True,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            },
            302: {
                "concept_id": 302,
                "concept_name": "Endocrine disorder",
                "concept_code": "302",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                "standard_concept": True,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            },
            401: {
                "concept_id": 401,
                "concept_name": "Child concept",
                "concept_code": "401",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                "standard_concept": True,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            },
            555: {
                "concept_id": 555,
                "concept_name": "Source concept",
                "concept_code": "SRC1",
                "vocabulary_id": "SNOMED",
                "domain_id": "Observation",
                "concept_class_id": "Observable Entity",
                "standard_concept": False,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            },
        }
        return concepts.get(concept_id)

    def get_concept_by_code(self, vocabulary_id: str, code: str):
        if vocabulary_id == "SNOMED" and code == "SRC1":
            return [self.get_concept(555)]
        return []

    def get_ancestors(self, concept_id: int, max_depth: int):
        if concept_id == 102:
            return [
                {"concept_id": 301, "concept_name": "Diabetes mellitus", "vocabulary_id": "SNOMED", "domain_id": "Condition", "standard_concept": True, "depth": 1},
                {"concept_id": 302, "concept_name": "Endocrine disorder", "vocabulary_id": "SNOMED", "domain_id": "Condition", "standard_concept": True, "depth": 2},
            ]
        return []

    def get_descendants(self, concept_id: int, max_depth: int):
        if concept_id == 301:
            return [
                {"concept_id": 401, "concept_name": "Child concept", "vocabulary_id": "SNOMED", "domain_id": "Condition", "standard_concept": True, "depth": 1}
            ]
        return []

    def get_edges(self, concept_id: int):
        return {
            "outbound": [
                {"relationship_id": "Is a", "predicate_kind": "HIERARCHY", "target_concept_id": 301, "target_concept_name": "Diabetes mellitus", "valid": True}
            ],
            "inbound": [],
        }

    def get_neighbors(self, concept_id: int, max_depth: int, predicate_kinds, max_nodes: int, include_edges: bool):
        return {
            "concept_id": concept_id,
            "neighbor_count": 1,
            "edge_count": 0,
            "neighbors": [
                {"concept_id": 301, "concept_name": "Diabetes mellitus", "vocabulary_id": "SNOMED", "domain_id": "Condition", "concept_class_id": "Clinical Finding", "standard_concept": True}
            ],
            "edges": [],
            "terminated_early": False,
            "terminated_reason": None,
        }

    def ground(self, query: str, limit: int, domain: str | None, vocabulary_id: str | None, parent_ids=None):
        return {
            "results": [
                {
                    "concept_id": 102,
                    "concept_name": "Type 2 diabetes mellitus",
                    "match_kind": "PARTIAL",
                    "standard_concept": True,
                }
            ],
            "grounding_explanation": {"matched_tier": "PARTIAL"},
        }

    def find_path(self, source_id: int, target_id: int, max_depth: int, within_domain: bool = True):
        return {"found": True, "paths": [{"length": 1, "steps": [{"subject_id": source_id, "object_id": target_id, "predicate": "Is a", "predicate_kind": "HIERARCHY"}]}]}

    def map_to_standard(self, vocabulary_id: str, code: str):
        if code == "44054007":
            return {"source": self.get_concept(102), "standard_concepts": [self.get_concept(201)]}
        return {"source": self.get_concept(555), "standard_concepts": []}

    def concept_views(self, concept_ids):
        # Concept 104 is surfaced only by the embedding channel (StubEmbAdapter),
        # so it is not in get_concept(); expose it here so backfill can enrich it.
        embedding_only = {
            104: {
                "concept_id": 104,
                "concept_name": "Diabetes",
                "concept_code": "73211009",
                "vocabulary_id": "SNOMED",
                "domain_id": "Condition",
                "concept_class_id": "Clinical Finding",
                "standard_concept": True,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            },
        }
        out: dict[int, dict] = {}
        for cid in concept_ids:
            view = self.get_concept(cid) or embedding_only.get(cid)
            if view is not None:
                out[int(cid)] = view
        return out


class StubEmbAdapter:
    def search(self, query: str, limit: int, domain: str | None, vocabulary: str | None, standard_only: bool, active_only: bool, model_name: str | None):
        return {
            "query_text": query,
            "model_name": model_name or "demo-model",
            "results": [
                {"concept_id": 104, "concept_name": "Diabetes", "similarity": 0.91, "is_standard": True, "is_active": True}
            ],
        }

    def get_neighbours(self, concept_id: int, limit: int, model_name: str | None):
        return {
            "query_concept_id": concept_id,
            "model_name": model_name or "demo-model",
            "results": [{"concept_id": 888, "concept_name": "Near neighbour", "similarity": 0.88}],
        }


class StubGroundingService:
    def ground(
        self,
        query: str,
        *,
        limit: int,
        domain: str | None,
        vocabulary_id: str | None,
        parent_ids=None,
    ):
        return {
            "results": [
                {
                    "concept_id": 102,
                    "concept_name": "Type 2 diabetes mellitus",
                    "match_kind": "PARTIAL",
                    "standard_concept": True,
                }
            ],
            "grounding_explanation": {"matched_tier": "PARTIAL"},
        }


def build_service() -> MappingService:
    return MappingService(
        StubVocabAdapter(),
        graph_service=StubGraphAdapter(),
        emb_adapter=StubEmbAdapter(),
        grounding_service=StubGroundingService(),
    )


def test_concept_search_normalized_returns_normalized_metadata_and_results():
    service = build_service()

    result = service.concept_search_normalized(" Type-2 Diabetes, NOS ")

    assert result["normalized_query"] == "type 2 diabetes"
    assert result["results"][0]["match_mode"] == "label_exact_normalized"
    assert result["results"][0]["matched_text_normalized"] == "type 2 diabetes mellitus"


def test_concept_candidate_bundle_combines_channels_and_standard_mappings():
    service = build_service()

    result = service.concept_candidate_bundle(
        "type 2 diabetes",
        include_hierarchy_context=True,
        include_relationship_summary=True,
    )

    assert set(result["channels"]) >= {"exact", "normalized", "fulltext", "embedding"}
    assert any(row["concept_id"] == 102 for row in result["candidate_union"])
    non_standard = next(row for row in result["candidate_union"] if row["concept_id"] == 102)
    assert non_standard["mapped_standard_concepts"][0]["concept_id"] == 201
    assert "ancestor_preview" in non_standard


@pytest.mark.parametrize(
    "limits",
    (
        {"per_channel_limit": 0},
        {"per_channel_limit": 21},
        {"overall_limit": 0},
        {"overall_limit": 101},
    ),
)
def test_candidate_bundle_bounds_are_enforced_by_the_service(limits):
    with pytest.raises(ValueError, match="limit must be between"):
        build_service().concept_candidate_bundle("diabetes", **limits)


def test_embedding_only_candidate_backfilled_with_identity_metadata():
    """A candidate surfaced only by the embedding channel must not enter the
    union with null concept_code/vocabulary_id/domain_id/concept_class_id —
    omop-emb never supplies those, so the bundle backfills them via concept_views."""
    service = build_service()

    result = service.concept_candidate_bundle("type 2 diabetes")

    emb_only = next(row for row in result["candidate_union"] if row["concept_id"] == 104)
    assert "embedding" in emb_only["retrieved_by"]
    assert emb_only["vocabulary_id"] == "SNOMED"
    assert emb_only["concept_code"] == "73211009"
    assert emb_only["domain_id"] == "Condition"
    assert emb_only["concept_class_id"] == "Clinical Finding"


def test_embedding_only_candidate_warns_when_graph_absent_for_backfill():
    """Without a graph service the bundle cannot backfill identity metadata; it
    must surface a warning rather than silently returning null-metadata rows."""
    service = MappingService(StubVocabAdapter(), emb_adapter=StubEmbAdapter())

    result = service.concept_candidate_bundle("type 2 diabetes")

    emb_only = next(row for row in result["candidate_union"] if row["concept_id"] == 104)
    assert emb_only["vocabulary_id"] is None
    assert any("identity metadata" in w for w in result["warnings"])


def test_concept_nearest_standard_ancestor_selects_nearest_standard_ancestor():
    service = build_service()

    result = service.concept_nearest_standard_ancestor(query="type 2 diabetes", domain="Condition")

    assert result["found"] is True
    assert result["selected_parent"]["concept_id"] == 301
    assert result["selection_reason"] == "nearest_standard_ancestor"


def test_concept_nearest_standard_ancestor_by_concept_id():
    service = build_service()

    result = service.concept_nearest_standard_ancestor(concept_id=102)

    assert result["found"] is True
    assert result["selected_parent"]["concept_id"] == 301
    assert result["selection_reason"] == "direct_concept_input"


def test_concept_nearest_standard_ancestor_raises_when_no_graph():
    service = MappingService(StubVocabAdapter())

    from groundworkers.base.errors import GroundworkersError as GWError
    try:
        service.concept_nearest_standard_ancestor(query="diabetes")
        assert False, "should have raised"
    except GWError as exc:
        assert exc.code == "BACKEND_UNAVAIL"


def test_concept_mapping_context_assembles_requested_context():
    service = build_service()

    result = service.concept_mapping_context(
        102,
        include_embedding_neighbors=True,
        include_descendants=False,
    )

    assert result["concept"]["concept_id"] == 102
    assert result["standard_mapping"]["standard_concepts"][0]["concept_id"] == 201
    assert result["ancestors"][0]["concept_id"] == 301
    assert result["embedding_neighbors"][0]["concept_id"] == 888


def test_concept_map_to_value_returns_related_concepts():
    service = build_service()

    result = service.concept_map_to_value("SNOMED", "SRC1")

    assert result["source_concept"]["concept_id"] == 555
    assert result["maps_to_value"][0]["concept_id"] == 777


def test_concept_resolve_mapping_expression_applies_exclude_and_descendants():
    service = build_service()

    result = service.concept_resolve_mapping_expression(
        items=[
            {"concept_id": 301, "include_descendants": True},
            {"concept_id": 401, "exclude": True},
        ],
    )

    assert 301 in result["resolved_concept_ids"]
    assert 401 not in result["resolved_concept_ids"]
    assert result["counts"]["excluded"] == 1


def test_mapping_evaluate_candidates_computes_summary_metrics():
    service = build_service()

    result = service.mapping_evaluate_candidates(
        predicted_mappings=[
            {"source_key": "a", "predicted_standard_concept_ids": [201, 202], "domain_id": "Condition"},
            {"source_key": "b", "predicted_standard_concept_ids": [999], "domain_id": "Condition"},
        ],
        reference_mappings=[
            {"source_key": "a", "reference_standard_concept_id": 201, "domain_id": "Condition"},
            {"source_key": "b", "reference_standard_concept_id": 301, "domain_id": "Condition"},
            {"source_key": "c", "reference_standard_concept_id": 401, "domain_id": "Condition"},
        ],
    )

    assert result["summary_metrics"]["agreement_count"] == 1
    assert result["summary_metrics"]["disagreement_count"] == 1
    assert result["summary_metrics"]["missing_reference_count"] == 1


# ---------------------------------------------------------------------------
# Recording stub for propagation tests (Gap 8 and Gap 1)
# ---------------------------------------------------------------------------

class _RecordingVocabAdapter(StubVocabAdapter):
    """Wraps StubVocabAdapter and records the active_only / parent_ids received per channel."""

    def __init__(self):
        self.received: dict[str, dict] = {}

    def search_exact(self, query, *, active_only=False, parent_ids=None, **kwargs):
        self.received["exact"] = {"active_only": active_only, "parent_ids": parent_ids}
        return super().search_exact(query, **kwargs)

    def search_normalized(self, query, *, active_only=False, parent_ids=None, **kwargs):
        self.received["normalized"] = {"active_only": active_only, "parent_ids": parent_ids}
        return super().search_normalized(query, **kwargs)

    def search_fulltext(self, query, *, active_only=False, parent_ids=None, **kwargs):
        self.received["fulltext"] = {"active_only": active_only, "parent_ids": parent_ids}
        return super().search_fulltext(query, **kwargs)

    def navigate_to_standard(self, concept_ids):
        return []


def _service_with_recording_vocab(emb=None):
    vocab = _RecordingVocabAdapter()
    service = MappingService(vocab, graph_service=StubGraphAdapter(), emb_adapter=emb)
    return service, vocab


# ---------------------------------------------------------------------------
# active_only propagation (Gap 8)
# ---------------------------------------------------------------------------

def test_active_only_true_propagated_to_all_lexical_channels():
    service, vocab = _service_with_recording_vocab(emb=StubEmbAdapter())

    service.concept_candidate_bundle("diabetes", active_only=True)

    assert vocab.received["exact"]["active_only"] is True
    assert vocab.received["normalized"]["active_only"] is True
    assert vocab.received["fulltext"]["active_only"] is True


def test_active_only_false_propagated_to_all_lexical_channels():
    service, vocab = _service_with_recording_vocab(emb=StubEmbAdapter())

    service.concept_candidate_bundle("diabetes", active_only=False)

    assert vocab.received["exact"]["active_only"] is False
    assert vocab.received["normalized"]["active_only"] is False
    assert vocab.received["fulltext"]["active_only"] is False


# ---------------------------------------------------------------------------
# parent_ids propagation (Gap 1)
# ---------------------------------------------------------------------------

def test_parent_ids_propagated_to_all_lexical_channels():
    service, vocab = _service_with_recording_vocab(emb=StubEmbAdapter())

    service.concept_candidate_bundle("diabetes", parent_ids=[301])

    assert vocab.received["exact"]["parent_ids"] == [301]
    assert vocab.received["normalized"]["parent_ids"] == [301]
    assert vocab.received["fulltext"]["parent_ids"] == [301]


def test_parent_ids_none_propagated_when_not_specified():
    service, vocab = _service_with_recording_vocab(emb=StubEmbAdapter())

    service.concept_candidate_bundle("diabetes")

    assert vocab.received["exact"]["parent_ids"] is None
    assert vocab.received["normalized"]["parent_ids"] is None
    assert vocab.received["fulltext"]["parent_ids"] is None


def test_embedding_channel_notes_parent_ids_limitation_when_parent_ids_provided():
    service, _ = _service_with_recording_vocab(emb=StubEmbAdapter())

    result = service.concept_candidate_bundle("diabetes", parent_ids=[301])

    emb_notes = result["channels"]["embedding"]["retrieval_notes"]
    assert any("parent_ids" in note for note in emb_notes)


def test_concept_nearest_standard_ancestor_navigates_ancestors_for_exact_non_standard_match():
    """An EXACT label match on a non-standard concept must NOT take the early-return path
    (exact_standard_match); it must navigate to a standard ancestor instead."""

    class ExactNonStandardGroundingService:
        def ground(self, query, *, limit, domain, vocabulary_id, parent_ids=None):
            return {
                "results": [{
                    "concept_id": 102,
                    "concept_name": "Type 2 diabetes mellitus",
                    "match_kind": "EXACT",
                    "standard_concept": False,
                }],
                "grounding_explanation": {"matched_tier": "EXACT"},
            }

    service = MappingService(
        StubVocabAdapter(),
        graph_service=StubGraphAdapter(),
        grounding_service=ExactNonStandardGroundingService(),
    )
    result = service.concept_nearest_standard_ancestor(query="Type 2 diabetes mellitus")

    assert result["found"] is True
    assert result["selection_reason"] == "nearest_standard_ancestor", (
        "EXACT match on a non-standard concept must not short-circuit to exact_standard_match"
    )
    assert result["selected_parent"] is not None
    assert result["selected_parent"]["concept_id"] == 301


def test_embedding_channel_no_parent_ids_note_when_parent_ids_absent():
    service, _ = _service_with_recording_vocab(emb=StubEmbAdapter())

    result = service.concept_candidate_bundle("diabetes")

    emb_notes = result["channels"]["embedding"]["retrieval_notes"]
    assert not any("parent_ids" in note for note in emb_notes)


# ---------------------------------------------------------------------------
# Constraints echoed correctly in bundle output
# ---------------------------------------------------------------------------

def test_candidate_bundle_constraints_reflect_active_only_and_parent_ids():
    service, _ = _service_with_recording_vocab()

    result = service.concept_candidate_bundle("diabetes", active_only=True, parent_ids=[301, 302])

    constraints = result["constraints"]
    assert constraints["active_only"] is True
    assert constraints["parent_ids"] == [301, 302]
