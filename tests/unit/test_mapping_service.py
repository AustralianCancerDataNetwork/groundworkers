from unittest.mock import Mock

import pytest

from groundworkers.base.errors import GroundworkersError
from groundworkers.services import MappingService
from groundworkers.services.vocab import (
    ConceptMappingResult,
    ConceptMatch,
    MappedConcept,
)


class StubVocabAdapter:
    def search_exact(
        self,
        query: str,
        *,
        domain=None,
        vocabulary_id=None,
        standard_only=False,
        active_only=False,
        include_synonyms=True,
        parent_ids=None,
        limit=20,
    ):
        return [
            ConceptMatch(
                concept={
                    "concept_id": 101,
                    "concept_name": "Diabetes mellitus",
                    "concept_code": "44054006",
                    "vocabulary_id": "SNOMED",
                    "domain_id": "Condition",
                    "concept_class_id": "Clinical Finding",
                    "standard_concept": True,
                    "classification_concept": False,
                    "is_active": True,
                },
                match_source="name",
            )
        ]

    def search_normalized(
        self,
        query: str,
        *,
        domain=None,
        vocabulary_id=None,
        standard_only=False,
        active_only=False,
        include_synonyms=False,
        normalization_profile="verbatim",
        parent_ids=None,
        remove_stop_phrases=True,
        limit=20,
        allow_unindexed_scan=False,
    ):
        return [
            ConceptMatch(
                concept={
                    "concept_id": 102,
                    "concept_name": "Type 2 diabetes mellitus",
                    "concept_code": "44054007",
                    "vocabulary_id": "SNOMED",
                    "domain_id": "Condition",
                    "concept_class_id": "Clinical Finding",
                    "standard_concept": False,
                    "classification_concept": False,
                    "is_active": True,
                },
                match_source="name",
            )
        ]

    def search_normalized_with_plan(self, query: str, **kwargs):
        scoped = bool(
            kwargs.get("domain")
            or kwargs.get("vocabulary_id")
            or kwargs.get("parent_ids")
        )
        if not scoped and not kwargs.get("allow_unindexed_scan"):
            raise GroundworkersError(
                "INVALID_INPUT",
                "Build indexes via Setup → Performance → normalised-name indexes, or pass domain, vocabulary_id, or parent_ids.",
            )
        return self.search_normalized(query, **kwargs), (
            "scoped_scan" if scoped else "unindexed_scan"
        )

    def search_fulltext(
        self,
        query: str,
        *,
        domain=None,
        vocabulary_id=None,
        standard_only=False,
        active_only=False,
        include_synonyms=True,
        parent_ids=None,
        min_rank=0.0,
        limit=20,
    ):
        return (
            [
                ConceptMatch(
                    concept={
                        "concept_id": 103,
                        "concept_name": "Diabetic disorder",
                        "concept_code": "44054008",
                        "vocabulary_id": "SNOMED",
                        "domain_id": "Condition",
                        "concept_class_id": "Clinical Finding",
                        "standard_concept": False,
                        "classification_concept": False,
                        "is_active": True,
                    },
                    match_source="name",
                    ts_rank=0.42,
                )
            ],
            True,
        )

    def navigate_to_standard(self, concept_ids: list[int]):
        return [
            ConceptMappingResult(
                source_concept_id=102,
                source_concept_name="Type 2 diabetes mellitus",
                source_standard_concept=False,
                source_classification_concept=False,
                targets=[
                    MappedConcept(
                        # A shared concept payload, not a parallel field list.
                        concept={
                            "concept_id": 201,
                            "concept_name": "Type 2 diabetes mellitus (standard)",
                            "vocabulary_id": "SNOMED",
                            "domain_id": "Condition",
                            "concept_code": "44054006",
                            "concept_class_id": "Clinical Finding",
                            "standard_concept": True,
                            "classification_concept": False,
                            "is_active": True,
                        },
                        relationship_id="Maps to",
                    )
                ],
            )
        ]

    def navigate_to_value(self, concept_ids: list[int]):
        return [
            ConceptMappingResult(
                source_concept_id=555,
                source_concept_name="Source concept",
                source_standard_concept=False,
                source_classification_concept=False,
                targets=[
                    MappedConcept(
                        concept={
                            "concept_id": 777,
                            "concept_name": "Positive",
                            "vocabulary_id": "SNOMED",
                            "domain_id": "Observation",
                            "concept_code": "10828004",
                            "concept_class_id": "Qualifier Value",
                            "standard_concept": True,
                            "classification_concept": False,
                            "is_active": True,
                        },
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
                {
                    "concept_id": 301,
                    "concept_name": "Diabetes mellitus",
                    "vocabulary_id": "SNOMED",
                    "domain_id": "Condition",
                    "standard_concept": True,
                    "depth": 1,
                },
                {
                    "concept_id": 302,
                    "concept_name": "Endocrine disorder",
                    "vocabulary_id": "SNOMED",
                    "domain_id": "Condition",
                    "standard_concept": True,
                    "depth": 2,
                },
            ]
        return []

    def get_descendants(self, concept_id: int, max_depth: int):
        if concept_id == 301:
            return [
                {
                    "concept_id": 401,
                    "concept_name": "Child concept",
                    "vocabulary_id": "SNOMED",
                    "domain_id": "Condition",
                    "standard_concept": True,
                    "depth": 1,
                }
            ]
        return []

    def get_edges(self, concept_id: int):
        return {
            "outbound": [
                {
                    "relationship_id": "Is a",
                    "predicate_kind": "HIERARCHY",
                    "target_concept_id": 301,
                    "target_concept_name": "Diabetes mellitus",
                    "valid": True,
                }
            ],
            "inbound": [],
        }

    def get_neighbors(
        self,
        concept_id: int,
        max_depth: int,
        predicate_kinds,
        max_nodes: int,
        include_edges: bool,
    ):
        return {
            "concept_id": concept_id,
            "neighbor_count": 1,
            "edge_count": 0,
            "neighbors": [
                {
                    "concept_id": 301,
                    "concept_name": "Diabetes mellitus",
                    "vocabulary_id": "SNOMED",
                    "domain_id": "Condition",
                    "concept_class_id": "Clinical Finding",
                    "standard_concept": True,
                }
            ],
            "edges": [],
            "terminated_early": False,
            "terminated_reason": None,
        }

    def ground(
        self,
        query: str,
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

    def find_path(
        self, source_id: int, target_id: int, max_depth: int, within_domain: bool = True
    ):
        return {
            "found": True,
            "paths": [
                {
                    "length": 1,
                    "steps": [
                        {
                            "subject_id": source_id,
                            "object_id": target_id,
                            "predicate": "Is a",
                            "predicate_kind": "HIERARCHY",
                        }
                    ],
                }
            ],
        }

    def map_to_standard(self, vocabulary_id: str, code: str):
        if code == "44054007":
            return {
                "source": self.get_concept(102),
                "standard_concepts": [self.get_concept(201)],
            }
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
    def search(
        self,
        query: str,
        limit: int,
        domain: str | None,
        vocabulary: str | None,
        standard_only: bool,
        active_only: bool,
        model_name: str | None,
    ):
        return {
            "query_text": query,
            "model_name": model_name or "demo-model",
            "results": [
                {
                    "concept_id": 104,
                    "concept_name": "Diabetes",
                    "similarity": 0.91,
                    "is_standard": True,
                    "is_active": True,
                }
            ],
        }

    def get_neighbours(self, concept_id: int, limit: int, model_name: str | None):
        return {
            "query_concept_id": concept_id,
            "model_name": model_name or "demo-model",
            "results": [
                {
                    "concept_id": 888,
                    "concept_name": "Near neighbour",
                    "similarity": 0.88,
                }
            ],
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


def build_service(graph: StubGraphAdapter | None = None) -> MappingService:
    return MappingService(
        StubVocabAdapter(),
        graph_service=graph or StubGraphAdapter(),
        emb_adapter=StubEmbAdapter(),
        grounding_service=StubGroundingService(),
    )


def test_concept_search_normalized_returns_normalized_metadata_and_results():
    service = build_service()

    result = service.concept_search_normalized(
        " Type-2 Diabetes, NOS ", allow_unindexed_scan=True
    )

    assert result["normalized_query"] == "type 2 diabetes"
    assert result["plan"] == "unindexed_scan"
    assert result["results"][0]["match_mode"] == "label_exact_normalized"
    assert result["results"][0]["matched_text_normalized"] == "type 2 diabetes mellitus"


def test_concept_search_normalized_accepts_parent_scope():
    result = build_service().concept_search_normalized("diabetes", parent_ids=[101])

    assert result["plan"] == "scoped_scan"


def test_concept_candidate_bundle_combines_channels_and_standard_mappings():
    service = build_service()

    result = service.concept_candidate_bundle(
        "type 2 diabetes",
        vocabulary_id="ICD10CM",
        include_hierarchy_context=True,
        include_relationship_summary=True,
    )

    assert set(result["channels"]) >= {"exact", "normalized", "fulltext", "embedding"}
    assert any(row["concept_id"] == 102 for row in result["candidate_union"])
    non_standard = next(
        row for row in result["candidate_union"] if row["concept_id"] == 102
    )
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

    emb_only = next(
        row for row in result["candidate_union"] if row["concept_id"] == 104
    )
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

    emb_only = next(
        row for row in result["candidate_union"] if row["concept_id"] == 104
    )
    assert emb_only["vocabulary_id"] is None
    assert any("identity metadata" in w for w in result["warnings"])


def test_concept_nearest_standard_ancestor_selects_nearest_standard_ancestor():
    service = build_service()

    result = service.concept_nearest_standard_ancestor(
        query="type 2 diabetes", domain="Condition"
    )

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


def _shared_ancestor_graph(ancestor_rows):
    graph = Mock()
    graph.get_stored_ancestor_maps.return_value = {
        cid: {
            cid: {
                "concept_id": cid,
                "domain_id": "Condition",
                "standard_concept": False,
            },
            **{row["concept_id"]: row for row in ancestor_rows},
        }
        for cid in (801, 802)
    }
    return graph


def test_concept_shared_standard_ancestor_selects_unique_nearest_common_parent():
    graph = _shared_ancestor_graph(
        [
            {
                "concept_id": 803,
                "domain_id": "Condition",
                "standard_concept": True,
                "depth": 1,
            },
            {
                "concept_id": 804,
                "domain_id": "Condition",
                "standard_concept": True,
                "depth": 2,
            },
        ]
    )
    result = build_service(graph).concept_shared_standard_ancestor([801, 802])
    assert result["found"] is True
    assert result["selected_ancestor"]["concept_id"] == 803
    assert result["selected_ancestor"]["depth"] == 1
    graph.get_stored_ancestor_maps.assert_called_once_with([801, 802], 5)


def test_concept_shared_standard_ancestor_leaves_equal_depth_tie_unresolved():
    graph = _shared_ancestor_graph(
        [
            {
                "concept_id": cid,
                "domain_id": "Condition",
                "standard_concept": True,
                "depth": 1,
            }
            for cid in (803, 804)
        ]
    )
    result = build_service(graph).concept_shared_standard_ancestor([801, 802])
    assert result["found"] is False
    assert result["selection_reason"] == "multiple_nearest_shared_ancestors"
    assert result["selected_ancestor"] is None
    assert [row["concept_id"] for row in result["common_ancestors"]] == [803, 804]


def test_concept_shared_standard_ancestor_requires_graph():
    service = MappingService(StubVocabAdapter())

    from groundworkers.base.errors import GroundworkersError as GWError

    with pytest.raises(GWError, match="omop_graph"):
        service.concept_shared_standard_ancestor([801, 802])


def test_concept_relationship_neighborhood_bounds_depth_and_assembles_unique_edges():
    graph = Mock()
    edge_data = {
        801: [
            {
                "target_concept_id": 802,
                "relationship_id": "Tradename of",
                "valid": True,
            },
            {
                "target_concept_id": 802,
                "relationship_id": "Tradename of",
                "valid": True,
            },
        ],
        802: [{"target_concept_id": 803, "relationship_id": "Box of", "valid": True}],
        803: [{"target_concept_id": 804, "relationship_id": "Box of", "valid": True}],
    }
    graph.get_relationship_edges.side_effect = lambda cid, relationships, limit: (
        edge_data[cid][:limit]
    )
    graph.concept_views.side_effect = lambda ids, **kw: {
        cid: {"concept_id": cid} for cid in ids
    }
    service = MappingService(StubVocabAdapter(), graph_service=graph)

    result = service.concept_relationship_neighborhood(
        [801], relationship_ids=["Tradename of", "Box of"], max_depth=2
    )

    assert result["reached_by_seed"]["801"] == [
        {"concept_id": 801, "depth": 0},
        {"concept_id": 802, "depth": 1},
        {"concept_id": 803, "depth": 2},
    ]
    assert result["nodes"] == [{"concept_id": cid} for cid in (801, 802, 803)]
    assert result["edges"] == [
        {"subject_id": 801, "object_id": 802, "relationship_id": "Tradename of"},
        {"subject_id": 802, "object_id": 803, "relationship_id": "Box of"},
    ]
    assert result["truncated_seeds"] == []
    assert graph.get_relationship_edges.call_count == 2
    graph.get_relationship_edges.assert_any_call(801, ["Box of", "Tradename of"], 1001)
    graph.get_relationship_edges.assert_any_call(802, ["Box of", "Tradename of"], 1001)


def test_concept_relationship_neighborhood_requires_relationship_ids():
    service = MappingService(StubVocabAdapter(), graph_service=Mock())

    with pytest.raises(ValueError, match="relationship_ids"):
        service.concept_relationship_neighborhood([801], relationship_ids=[])


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
            {
                "source_key": "a",
                "predicted_standard_concept_ids": [201, 202],
                "domain_id": "Condition",
            },
            {
                "source_key": "b",
                "predicted_standard_concept_ids": [999],
                "domain_id": "Condition",
            },
        ],
        reference_mappings=[
            {
                "source_key": "a",
                "reference_standard_concept_id": 201,
                "domain_id": "Condition",
            },
            {
                "source_key": "b",
                "reference_standard_concept_id": 301,
                "domain_id": "Condition",
            },
            {
                "source_key": "c",
                "reference_standard_concept_id": 401,
                "domain_id": "Condition",
            },
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
        self.received["normalized"] = {
            "active_only": active_only,
            "parent_ids": parent_ids,
        }
        return super().search_normalized(query, **kwargs)

    def search_fulltext(self, query, *, active_only=False, parent_ids=None, **kwargs):
        self.received["fulltext"] = {
            "active_only": active_only,
            "parent_ids": parent_ids,
        }
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

    service.concept_candidate_bundle(
        "diabetes", vocabulary_id="ICD10CM", active_only=True
    )

    assert vocab.received["exact"]["active_only"] is True
    assert vocab.received["normalized"]["active_only"] is True
    assert vocab.received["fulltext"]["active_only"] is True


def test_active_only_false_propagated_to_all_lexical_channels():
    service, vocab = _service_with_recording_vocab(emb=StubEmbAdapter())

    service.concept_candidate_bundle(
        "diabetes", vocabulary_id="ICD10CM", active_only=False
    )

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

    service.concept_candidate_bundle("diabetes", vocabulary_id="ICD10CM")

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
                "results": [
                    {
                        "concept_id": 102,
                        "concept_name": "Type 2 diabetes mellitus",
                        "match_kind": "EXACT",
                        "standard_concept": False,
                    }
                ],
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

    result = service.concept_candidate_bundle(
        "diabetes", active_only=True, parent_ids=[301, 302]
    )

    constraints = result["constraints"]
    assert constraints["active_only"] is True
    assert constraints["parent_ids"] == [301, 302]


# ---------------------------------------------------------------------------
# concept_nearest_standard_ancestor: failure is reported as failure
# ---------------------------------------------------------------------------
#
# The fallback used to return the seed concept itself with found=True, so a
# caller could not tell "here is the standard ancestor you asked for" from
# "there is none and this is your own input handed back".


class _NoAncestryGraph(StubGraphAdapter):
    """A seed with no ancestry and no ``Maps to`` target.

    Mirrors a non-standard concept whose vocabulary has no ``concept_ancestor``
    rows — ICD10CM is the real-world case.
    """

    def get_concept(self, concept_id: int):
        if concept_id == 555:
            return {
                "concept_id": 555,
                "concept_name": "Orphan non-standard concept",
                "concept_code": "ORPHAN",
                "vocabulary_id": "ICD10CM",
                "domain_id": "Condition",
                "concept_class_id": "4-char billing code",
                "standard_concept": False,
                "valid_start_date": "2000-01-01",
                "valid_end_date": "2099-12-31",
                "invalid_reason": None,
            }
        return super().get_concept(concept_id)

    def get_ancestors(self, concept_id: int, max_depth: int):
        return [] if concept_id == 555 else super().get_ancestors(concept_id, max_depth)


def test_nearest_standard_ancestor_reports_failure_when_none_exists():
    service = build_service(graph=_NoAncestryGraph())

    result = service.concept_nearest_standard_ancestor(concept_id=555)

    assert result["found"] is False
    assert result["selected_parent"] is None
    assert result["selection_reason"] == "no_standard_ancestor"
    assert result["warnings"], "a degraded result must say why"


def test_nearest_standard_ancestor_never_returns_a_non_standard_parent():
    """The core contract: found=True implies a genuinely standard target."""
    service = build_service(graph=_NoAncestryGraph())

    result = service.concept_nearest_standard_ancestor(concept_id=555)

    if result["found"]:
        assert result["selected_parent"]["standard_concept"] is True
    else:
        assert result["selected_parent"] is None


def test_nearest_standard_ancestor_falls_back_to_maps_to():
    """A seed with no ancestry but a 'Maps to' target is still resolvable.

    Concept 102 has ancestors in the default stub, so this exercises the branch
    via a graph where ancestry is empty but the mapping exists.
    """

    class _MapsToOnlyGraph(StubGraphAdapter):
        def get_ancestors(self, concept_id: int, max_depth: int):
            return []

    service = build_service(graph=_MapsToOnlyGraph())

    result = service.concept_nearest_standard_ancestor(concept_id=102)

    assert result["found"] is True
    assert result["selection_reason"] == "maps_to_standard"
    assert result["selected_parent"]["concept_id"] == 201
    assert result["warnings"] == []


def test_nearest_standard_ancestor_still_succeeds_through_ancestry():
    """Regression guard: the passing path must be unaffected."""
    service = build_service()

    result = service.concept_nearest_standard_ancestor(concept_id=102)

    assert result["found"] is True
    assert result["selected_parent"]["concept_id"] == 301
    assert result["warnings"] == []


def test_candidate_bundle_omits_the_normalized_channel_when_it_would_scan():
    service = MappingService(StubVocabAdapter(), graph_service=StubGraphAdapter())

    result = service.concept_candidate_bundle("diabetes")

    assert "normalized" not in result["channels"]
    assert any(
        "normalized channel omitted" in warning for warning in result["warnings"]
    )


def test_candidate_bundle_keeps_the_normalized_channel_when_a_vocabulary_bounds_it():
    service = MappingService(StubVocabAdapter(), graph_service=StubGraphAdapter())

    result = service.concept_candidate_bundle("diabetes", vocabulary_id="SNOMED")

    assert result["channels"]["normalized"]["available"] is True
    assert not any(
        "normalized channel omitted" in warning for warning in result["warnings"]
    )


def test_candidate_bundle_keeps_a_failed_timeout_channel_as_unavailable():
    class TimedOutVocab(StubVocabAdapter):
        def search_normalized_with_plan(self, _query, **_kwargs):
            raise GroundworkersError("QUERY_TIMEOUT", "normalized scan timed out")

    service = MappingService(TimedOutVocab(), graph_service=StubGraphAdapter())

    result = service.concept_candidate_bundle("diabetes", domain="Condition")

    assert result["channels"]["normalized"]["available"] is False
    assert result["channels"]["normalized"]["retrieval_notes"] == [
        "normalized scan timed out"
    ]
    assert any(
        "normalized channel timed out" in warning for warning in result["warnings"]
    )


def test_relationship_neighborhood_bounds_edges_and_reports_truncation():
    graph = Mock()
    graph.get_relationship_edges.side_effect = lambda cid, relationships, limit: [
        {"target_concept_id": n, "relationship_id": "Box of", "valid": True}
        for n in range(2, 1002)
    ][:limit]
    graph.concept_views.side_effect = lambda ids, **kw: {
        cid: {"concept_id": cid} for cid in ids
    }
    result = MappingService(
        StubVocabAdapter(), graph_service=graph
    ).concept_relationship_neighborhood(
        [1], relationship_ids=["Box of"], max_depth=1, max_nodes=10
    )
    assert len(result["nodes"]) == 10
    assert len(result["edges"]) == 9
    assert result["truncated_seeds"] == [1]
    assert {node["concept_id"] for node in result["nodes"]} == set(range(1, 11))
    assert graph.concept_views.call_count == 1


def test_relationship_neighborhood_rejects_incomplete_metadata():
    graph = Mock()
    graph.get_relationship_edges.return_value = [
        {"target_concept_id": 2, "relationship_id": "Box of", "valid": True}
    ]
    graph.concept_views.return_value = {1: {"concept_id": 1}}
    with pytest.raises(GroundworkersError, match="metadata is incomplete") as raised:
        MappingService(
            StubVocabAdapter(), graph_service=graph
        ).concept_relationship_neighborhood(
            [1], relationship_ids=["Box of"], max_depth=1
        )
    assert raised.value.code == "QUERY_ERROR"
    graph.concept_views.assert_called_once_with((1, 2), strict=True)


@pytest.mark.parametrize("seeds", [[999999], [1, 999999], [999999, 888888]])
def test_relationship_neighborhood_reports_unknown_seeds(seeds):
    graph = Mock()
    graph.get_relationship_edges.return_value = []
    graph.concept_views.return_value = {1: {"concept_id": 1}}
    with pytest.raises(GroundworkersError) as raised:
        MappingService(
            StubVocabAdapter(), graph_service=graph
        ).concept_relationship_neighborhood(seeds, relationship_ids=["Box of"])
    assert raised.value.code == "NOT_FOUND"
    assert str(sorted(set(seeds) - {1})) in raised.value.message
    graph.concept_views.assert_called_once_with(tuple(sorted(seeds)), strict=True)


@pytest.mark.parametrize("seeds", [[1, 20], [20, 1]])
def test_relationship_neighborhood_gives_each_seed_its_own_edge_budget(seeds):
    graph = Mock()
    # Dense edges among ten nodes exhaust the 50-edge budget without hitting the node cap.
    edge_data = {
        cid: [
            {"target_concept_id": target, "relationship_id": "Box of", "valid": True}
            for target in range(1, 7)
        ]
        for cid in range(1, 10)
    }
    edge_data[1] = [
        {"target_concept_id": target, "relationship_id": "Box of", "valid": True}
        for target in range(2, 11)
    ]
    edge_data[20] = [
        {"target_concept_id": 21, "relationship_id": "Box of", "valid": True}
    ]
    graph.get_relationship_edges.side_effect = lambda cid, relationships, limit: (
        edge_data.get(cid, [])[:limit]
    )
    graph.concept_views.side_effect = lambda ids, **kw: {
        cid: {"concept_id": cid} for cid in ids
    }
    service = MappingService(StubVocabAdapter(), graph_service=graph)

    result = service.concept_relationship_neighborhood(
        seeds, relationship_ids=["Box of"], max_depth=2, max_nodes=10
    )
    reverse = service.concept_relationship_neighborhood(
        seeds[::-1], relationship_ids=["Box of"], max_depth=2, max_nodes=10
    )

    assert result["truncated_seeds"] == reverse["truncated_seeds"] == [1]
    assert result["nodes"] == reverse["nodes"]
    assert result["reached_by_seed"] == reverse["reached_by_seed"]
    assert {tuple(sorted(edge.items())) for edge in result["edges"]} == {
        tuple(sorted(edge.items())) for edge in reverse["edges"]
    }
    assert result["max_edges_per_seed"] == 50
    assert len(result["edges"]) == result["returned_edge_count"] == 51
    assert reverse["returned_edge_count"] == 51
    assert {"subject_id": 20, "object_id": 21, "relationship_id": "Box of"} in result[
        "edges"
    ]


def test_relationship_neighborhood_reuses_fetches_and_merges_overlapping_walks():
    graph = Mock()
    edge_data = {
        1: [{"target_concept_id": 2, "relationship_id": "Box of", "valid": True}],
        2: [{"target_concept_id": 3, "relationship_id": "Box of", "valid": True}],
    }
    graph.get_relationship_edges.side_effect = lambda cid, relationships, limit: (
        edge_data.get(cid, [])[:limit]
    )
    graph.concept_views.side_effect = lambda ids, **kw: {
        cid: {"concept_id": cid} for cid in ids
    }

    result = MappingService(
        StubVocabAdapter(), graph_service=graph
    ).concept_relationship_neighborhood(
        [1, 2], relationship_ids=["Box of"], max_depth=2, max_nodes=10
    )

    assert result["nodes"] == [{"concept_id": cid} for cid in (1, 2, 3)]
    assert result["edges"] == [
        {"subject_id": 1, "object_id": 2, "relationship_id": "Box of"},
        {"subject_id": 2, "object_id": 3, "relationship_id": "Box of"},
    ]
    assert result["reached_by_seed"]["2"] == [
        {"concept_id": 2, "depth": 0},
        {"concept_id": 3, "depth": 1},
    ]
    assert result["truncated_seeds"] == []
    assert graph.get_relationship_edges.call_count == 3
    graph.get_relationship_edges.assert_any_call(2, ["Box of"], 51)


@pytest.mark.parametrize("seeds", [[1, 2], [2, 1]])
def test_exhausted_relationship_walk_only_probes_and_preserves_other_seed_budgets(
    seeds,
):
    from groundworkers.services.relationship_neighborhood import RelationshipTraversal

    graph = Mock()
    adjacency = {1: [2, 3], 2: list(range(4, 104))}
    graph.get_relationship_edges.side_effect = lambda cid, relationships, limit: [
        {"target_concept_id": target, "relationship_id": "Box of"}
        for target in adjacency.get(cid, [])[:limit]
    ]
    traversal = RelationshipTraversal(
        graph, ["Box of"], max_depth=2, max_nodes=10, max_edges=2
    )
    walks = {seed: traversal.walk(seed) for seed in seeds}
    assert set(walks[1].edges) == {(1, 2, "Box of"), (1, 3, "Box of")}
    assert set(walks[2].edges) == {(2, 4, "Box of"), (2, 5, "Box of")}
    assert all(walk.truncated for walk in walks.values())
    full_fetches = [
        call.args[0]
        for call in graph.get_relationship_edges.call_args_list
        if call.args[2] > 1
    ]
    assert sorted(full_fetches) == [1, 2]
    probes = [
        call.args[0]
        for call in graph.get_relationship_edges.call_args_list
        if call.args[2] == 1
    ]
    assert probes == ([2] if seeds[0] == 1 else [])
    assert len(traversal.edge_cache[2]) == 3


def test_exact_edge_budget_with_no_further_edges_is_not_truncated():
    from groundworkers.services.relationship_neighborhood import RelationshipTraversal

    graph = Mock()
    graph.get_relationship_edges.side_effect = lambda cid, relationships, limit: [
        {"target_concept_id": target, "relationship_id": "Box of"}
        for target in ([2, 3] if cid == 1 else [])[:limit]
    ]
    result = RelationshipTraversal(
        graph, ["Box of"], max_depth=2, max_nodes=10, max_edges=2
    ).walk(1)
    assert len(result.edges) == 2
    assert result.truncated is False
    assert [
        (call.args[0], call.args[2])
        for call in graph.get_relationship_edges.call_args_list
    ] == [(1, 3), (2, 1), (3, 1)]
