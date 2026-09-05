"""One wire shape for concepts, owned by ``base.concept_payload``.

These pin the property the module exists for: a field added to the upstream
concept view reaches every payload that declares the relevant detail level,
without a call site being edited. Five hand-built dicts previously meant
``classification_concept`` reached two of them.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from groundworkers.base.concept_payload import (
    project_payload,
    serialise_concept_view,
    serialise_nearest_match,
)


def _view(**overrides):
    """Mirrors omop_graph.graph.nodes.ConceptView."""
    base = dict(
        concept_id=201826,
        concept_name="Type 2 diabetes mellitus",
        concept_code="44054006",
        vocabulary_id="SNOMED",
        domain_id="Condition",
        concept_class_id="Disorder",
        standard_concept=True,
        classification_concept=False,
        valid_start_date=date(2002, 1, 31),
        valid_end_date=date(2099, 12, 31),
        invalid_reason=None,
        is_active=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDetailLevelsAreAdditive:
    """Levels are projections of one shape, not independent shapes."""

    def test_identity_is_a_subset_of_flags_is_a_subset_of_full(self):
        identity = set(serialise_concept_view(_view(), detail="identity"))
        flags = set(serialise_concept_view(_view(), detail="flags"))
        full = set(serialise_concept_view(_view(), detail="full"))

        assert identity < flags < full

    def test_shared_keys_carry_identical_values_across_levels(self):
        full = serialise_concept_view(_view(), detail="full")
        for level in ("identity", "flags"):
            narrower = serialise_concept_view(_view(), detail=level)
            assert all(full[key] == value for key, value in narrower.items())

    def test_projecting_full_reproduces_the_narrower_level(self):
        """Services receive full payloads and narrow them; the two must agree."""
        full = serialise_concept_view(_view(), detail="full")
        for level in ("identity", "flags", "full"):
            assert project_payload(full, detail=level) == serialise_concept_view(
                _view(), detail=level
            )


class TestFlagsAreAlwaysReportedTogether:
    """A single boolean cannot express standard / classification / neither."""

    @pytest.mark.parametrize(
        ("standard", "classification"),
        [(True, False), (False, True), (False, False)],
    )
    def test_both_flags_present_at_flag_detail(self, standard, classification):
        payload = serialise_concept_view(
            _view(standard_concept=standard, classification_concept=classification),
            detail="flags",
        )

        assert payload["standard_concept"] is standard
        assert payload["classification_concept"] is classification

    def test_neither_flag_leaks_at_identity_detail(self):
        payload = serialise_concept_view(_view(), detail="identity")

        assert "standard_concept" not in payload
        assert "classification_concept" not in payload


class TestNearestMatch:
    """omop-emb's fields must reach the wire, in the wire's vocabulary."""

    def _match(self, **overrides):
        base = dict(
            concept_id=45877606,
            concept_name="Diabetes Type 2",
            domain_id="Meas Value",
            vocabulary_id="LOINC",
            similarity=0.7456881,
            is_standard=True,
            is_classification=False,
            is_active=True,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_domain_and_vocabulary_are_not_discarded(self):
        """omop-emb 2.1 added these; the adapter used to drop them."""
        payload = serialise_nearest_match(self._match())

        assert payload["vocabulary_id"] == "LOINC"
        assert payload["domain_id"] == "Meas Value"

    def test_is_standard_is_translated_to_the_wire_vocabulary(self):
        payload = serialise_nearest_match(self._match())

        assert payload["standard_concept"] is True
        assert "is_standard" not in payload

    def test_classification_is_reported_when_the_sidecar_records_it(self):
        payload = serialise_nearest_match(
            self._match(is_standard=False, is_classification=True)
        )

        assert payload["standard_concept"] is False
        assert payload["classification_concept"] is True

    def test_missing_flags_stay_unknown(self):
        payload = serialise_nearest_match(
            self._match(is_standard=None, is_classification=None, is_active=None)
        )

        assert payload["standard_concept"] is None
        assert payload["is_active"] is None


class TestWireVocabularyIsUniform:
    def test_concept_and_embedding_payloads_agree_on_flag_names(self):
        concept = serialise_concept_view(_view(), detail="flags")
        embedding = serialise_nearest_match(
            SimpleNamespace(
                concept_id=1,
                concept_name="x",
                domain_id="Condition",
                vocabulary_id="SNOMED",
                similarity=1.0,
                is_standard=True,
                is_classification=False,
                is_active=True,
            )
        )

        shared = {"standard_concept", "classification_concept", "is_active"}
        assert shared <= set(concept)
        assert shared <= set(embedding)

    def test_dates_are_iso_strings(self):
        payload = serialise_concept_view(_view(), detail="full")

        assert payload["valid_start_date"] == "2002-01-31"
        assert payload["valid_end_date"] == "2099-12-31"
