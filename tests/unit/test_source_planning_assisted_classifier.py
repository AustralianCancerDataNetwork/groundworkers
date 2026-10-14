from __future__ import annotations

import asyncio

import pytest

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.assisted import AssistedColumnRoleClassifier
from groundworkers.services.source_planning.classifier import ColumnRoleClassifier
from groundworkers.services.source_planning.models import (
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
    RawTable,
    SourceFormat,
)
from groundworkers.services.source_planning.normalisation import normalise_table
from groundworkers.services.source_planning.service import SourcePlanningService


class ResponseModel:
    model = "fixture-model"
    provider = "fixture"

    def __init__(self, response):
        self.response = response
        self.sync_calls = 0
        self.async_calls = 0
        self.prompt = ""

    def complete_structured(self, prompt, _schema, **_kwargs):
        self.sync_calls += 1
        self.prompt = prompt
        return self.response

    async def async_complete_structured(self, prompt, _schema, **_kwargs):
        self.async_calls += 1
        self.prompt = prompt
        return self.response


def _baseline() -> AnnotatedTable:
    raw = RawTable(
        name="measurements",
        headers=["Code", "Label", "Mystery"],
        rows=[{"Code": "E11.9", "Label": "Diabetes", "Mystery": "A"}],
        sample_rows=[{"Code": "E11.9", "Label": "Diabetes", "Mystery": "A"}],
        source_format=SourceFormat.CSV,
        row_count=1,
    )
    return ColumnRoleClassifier().classify(normalise_table(raw))


def test_real_assisted_classifier_merges_only_candidates_and_recomputes_metadata():
    baseline = _baseline()
    model = ResponseModel(
        {
            "decisions": [
                {
                    "header": "Mystery",
                    "role": "values",
                    "confidence": 0.95,
                    "packed_value": True,
                    "notes": "coded response",
                },
                {"header": "Code", "role": "annotation", "confidence": 1.0},
                {"header": "Unknown", "role": "label", "confidence": 1.0},
            ]
        }
    )

    result = AssistedColumnRoleClassifier(model).classify(baseline=baseline)

    assert model.sync_calls == 1
    assert "Candidate headers to review:\nMystery" in model.prompt
    assert result.rows == baseline.rows
    assert result.column_annotations["Code"] == baseline.column_annotations["Code"]
    assert result.column_annotations["Mystery"].role is ColumnRole.values
    assert result.column_annotations["Mystery"].detection_tier == "LLM"
    assert result.packed_value_columns == ["Mystery"]
    assert result.fallback_columns == ["Mystery"]
    assert result.uncertain_columns == []
    assert result.llm_fallback_used is True
    assert result.groundable_column_count == 3
    assert result.classification_tier_used == "LLM"
    assert result.annotation_notes[-1].startswith("llm-assisted classification")


def test_async_classifier_uses_native_async_model_completion():
    baseline = _baseline()
    model = ResponseModel({"decisions": [{"header": "Mystery", "role": "codes", "confidence": 0.9}]})

    result = asyncio.run(AssistedColumnRoleClassifier(model).async_classify(baseline=baseline))

    assert model.async_calls == 1
    assert model.sync_calls == 0
    assert result.column_annotations["Mystery"].role is ColumnRole.codes


def test_classifier_does_not_call_model_when_no_columns_need_review():
    normalised = normalise_table(
        RawTable(
            name="known",
            headers=["Code"],
            rows=[{"Code": "E11.9"}],
            sample_rows=[{"Code": "E11.9"}],
            source_format=SourceFormat.CSV,
            row_count=1,
        )
    )
    baseline = AnnotatedTable.from_normalised(
        normalised,
        column_annotations={"Code": ColumnAnnotation(role=ColumnRole.codes, detection_tier="A", confidence=1)},
    )
    model = ResponseModel({"decisions": []})

    result = AssistedColumnRoleClassifier(model).classify(baseline=baseline)

    assert result is baseline
    assert model.sync_calls == 0


@pytest.mark.parametrize(
    "response",
    [None, {"decisions": [{"header": "Mystery", "role": "codes", "confidence": 2}]}, {"decisions": "bad"}],
)
def test_classifier_rejects_malformed_model_structure(response):
    with pytest.raises(GroundworkersError) as excinfo:
        AssistedColumnRoleClassifier(ResponseModel(response)).classify(baseline=_baseline())
    assert excinfo.value.code == "QUERY_ERROR"


def test_classifier_ignores_unrecognized_model_metadata_and_missing_decisions():
    baseline = _baseline()
    model = ResponseModel(
        {
            "reasoning": "extra provider metadata is tolerated",
            "decisions": [
                {
                    "header": "Mystery",
                    "role": "codes",
                    "confidence": 0.9,
                    "provider_trace": "ignored",
                }
            ],
        }
    )

    result = AssistedColumnRoleClassifier(model).classify(baseline=baseline)
    assert result.column_annotations["Mystery"].role is ColumnRole.codes

    empty = AssistedColumnRoleClassifier(ResponseModel({})).classify(baseline=baseline)
    assert empty.column_annotations == baseline.column_annotations


def test_duplicate_decisions_use_the_last_valid_candidate_decision():
    model = ResponseModel(
        {
            "decisions": [
                {"header": "Mystery", "role": "label", "confidence": 0.85},
                {"header": "Mystery", "role": "codes", "confidence": 0.9},
            ]
        }
    )

    result = AssistedColumnRoleClassifier(model).classify(baseline=_baseline())

    assert result.column_annotations["Mystery"].role is ColumnRole.codes


def test_unknown_headers_and_roles_are_ignored_without_changing_inputs():
    baseline = _baseline()
    model = ResponseModel(
        {
            "decisions": [
                {"header": "Mystery", "role": "not-a-role", "confidence": 0.9},
                {"header": "Not in the table", "role": "label", "confidence": 0.9},
            ]
        }
    )

    result = AssistedColumnRoleClassifier(model).classify(baseline=baseline)

    assert result.rows == baseline.rows
    assert "Mystery" not in result.column_annotations
    assert set(result.column_annotations) == set(baseline.column_annotations)


def test_source_planning_service_surfaces_assisted_provider_failure():
    class FailedModel:
        def complete_structured(self, *_args, **_kwargs):
            raise RuntimeError("fixture provider failure")

    service = SourcePlanningService(
        assisted_classifier=AssistedColumnRoleClassifier(FailedModel()),
    )
    with pytest.raises(RuntimeError, match="fixture provider failure"):
        service.plan_source_assisted(
            "Mystery,Other\nA,B\n", filename="input.csv"
        )
