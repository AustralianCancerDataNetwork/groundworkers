"""Behavioral contract for the temporary primitive and its upstream replacement."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from omop_alchemy.cdm.model.vocabulary import (
    Concept_Ancestor,
    Concept_Class,
    Vocabulary,
)
from sqlalchemy import create_engine

from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.services.graph import GraphService
from groundworkers.services.mapping import MappingService


@pytest.fixture
def adapter():
    engine = create_engine("sqlite:///:memory:").execution_options(
        schema_translate_map={"vocab": None, "primary": None, "results": None}
    )
    Concept_Ancestor.__table__.create(engine)
    Concept_Class.__table__.create(engine)
    Vocabulary.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            Concept_Class.__table__.insert(),
            [
                {
                    "concept_class_id": name,
                    "concept_class_name": name,
                    "concept_class_concept_id": 1,
                }
                for name in ["Clinical Drug", "Branded Drug"]
            ],
        )
        conn.execute(
            Vocabulary.__table__.insert(),
            [
                {
                    "vocabulary_id": name,
                    "vocabulary_name": name,
                    "vocabulary_concept_id": 1,
                }
                for name in ["RxNorm", "SNOMED"]
            ],
        )
    rows = [
        (1, 1, 0),
        (1, 10, 0),
        (2, 10, 1),
        (1, 20, 1),
        (2, 20, 1),
        (1, 30, 2),
        (2, 30, 2),
    ]
    with engine.begin() as conn:
        conn.execute(
            Concept_Ancestor.__table__.insert(),
            [
                {
                    "descendant_concept_id": child,
                    "ancestor_concept_id": parent,
                    "min_levels_of_separation": depth,
                    "max_levels_of_separation": depth + 1,
                }
                for child, parent, depth in rows
            ],
        )
    instance = OmopGraphAdapter(engine)
    yield instance
    engine.dispose()


def _mapping(adapter, monkeypatch, *, absent=(), inactive=()):
    def views(ids, *, strict=False):
        assert strict
        return {
            cid: {
                "concept_id": cid,
                "concept_name": str(cid),
                "domain_id": "Drug",
                "standard_concept": True,
                "is_active": cid not in inactive,
                "concept_class_id": "Branded Drug" if cid == 20 else "Clinical Drug",
                "vocabulary_id": "RxNorm",
            }
            for cid in ids
            if cid not in absent
        }

    monkeypatch.setattr(adapter, "concept_views", views)
    return MappingService(Mock(), graph_service=GraphService(adapter))


def test_query_keeps_distinct_zero_distance_and_excludes_self(adapter):
    rows = adapter.ancestor_rows([1, 1], max_separation=0)
    assert rows == [
        {
            "descendant_concept_id": 1,
            "ancestor_concept_id": 10,
            "min_levels_of_separation": 0,
            "max_levels_of_separation": 1,
        }
    ]
    assert len(adapter.ancestor_rows([1], max_separation=0, include_self=True)) == 2
    assert adapter.ancestor_rows([]) == []


@pytest.mark.parametrize(
    "ids,depth", [([0], 1), ([-1], 1), ([True], 1), ([1], -1), ([1], True)]
)
def test_query_rejects_invalid_ids_or_depth(adapter, ids, depth):
    with pytest.raises(ValueError):
        adapter.ancestor_rows(ids, max_separation=depth)


def test_shared_ancestor_preserves_zero_distances_and_cross_class_ties(
    adapter, monkeypatch
):
    mapping = _mapping(adapter, monkeypatch)
    generic = mapping.concept_shared_standard_ancestor([1, 2, 1])
    assert generic["selection_reason"] == "multiple_nearest_shared_ancestors"
    assert [(r["concept_id"], r["depth"]) for r in generic["common_ancestors"]] == [
        (10, 1),
        (20, 1),
        (30, 2),
    ]
    constrained = mapping.concept_shared_standard_ancestor(
        [1, 2], concept_class_ids=["Clinical Drug"], vocabulary_ids=["RxNorm"]
    )
    assert constrained["selected_ancestor"]["concept_id"] == 10
    assert constrained["selected_ancestor"]["depth"] == 1
    assert not mapping.concept_shared_standard_ancestor([1, 2], max_depth=0)["found"]
    with pytest.raises(ValueError):
        mapping.concept_shared_standard_ancestor([1, 2], concept_class_ids=[])
    assert not mapping.concept_shared_standard_ancestor(
        [1, 2], vocabulary_ids=["SNOMED"]
    )["found"]


def test_stored_maps_add_self_even_without_stored_self_row(adapter, monkeypatch):
    _mapping(adapter, monkeypatch)
    result = GraphService(adapter).get_stored_ancestor_maps([2], 0)
    assert result[2][2]["depth"] == 0


def test_inactive_ancestor_is_not_selected(adapter, monkeypatch):
    mapping = _mapping(adapter, monkeypatch, inactive=(10, 20))
    assert (
        mapping.concept_shared_standard_ancestor([1, 2])["selected_ancestor"][
            "concept_id"
        ]
        == 30
    )


@pytest.mark.parametrize("absent,code", [((2,), "NOT_FOUND"), ((10,), "QUERY_ERROR")])
def test_missing_concepts_fail_explicitly(adapter, monkeypatch, absent, code):
    mapping = _mapping(adapter, monkeypatch, absent=absent)
    with pytest.raises(GroundworkersError) as exc:
        mapping.concept_shared_standard_ancestor([1, 2])
    assert exc.value.code == code


def test_metadata_backend_failure_is_not_a_no_match(adapter, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(adapter, "_get_kg", lambda: SimpleNamespace(concept_views=fail))
    with pytest.raises(GroundworkersError):
        adapter.concept_views([1], strict=True)
    assert adapter.concept_views([1]) == {}


def test_ancestry_backend_failure_is_not_a_no_match(adapter, monkeypatch):
    def fail():
        raise RuntimeError("ancestry unavailable")

    monkeypatch.setattr(adapter, "_cdm_session_factory", fail)
    with pytest.raises(GroundworkersError):
        adapter.ancestor_rows([1])


def test_shared_ancestor_rejects_mixed_input_domains(adapter, monkeypatch):
    mapping = _mapping(adapter, monkeypatch)
    get_views = adapter.concept_views

    def mixed_views(ids, *, strict=False):
        result = get_views(ids, strict=strict)
        result[2]["domain_id"] = "Condition"
        return result

    monkeypatch.setattr(adapter, "concept_views", mixed_views)
    result = mapping.concept_shared_standard_ancestor([1, 2])
    assert result["selection_reason"] == "incompatible_domains"
    assert result["selected_ancestor"] is None


def test_shared_ancestor_can_select_an_input_itself_at_zero_distance(
    adapter, monkeypatch
):
    mapping = _mapping(adapter, monkeypatch)
    with adapter.engine.begin() as conn:
        conn.execute(
            Concept_Ancestor.__table__.insert(),
            {
                "descendant_concept_id": 2,
                "ancestor_concept_id": 1,
                "min_levels_of_separation": 0,
                "max_levels_of_separation": 0,
            },
        )
    result = mapping.concept_shared_standard_ancestor([1, 2], max_depth=0)
    assert result["selected_ancestor"]["concept_id"] == 1
    assert result["selected_ancestor"]["depth"] == 0
