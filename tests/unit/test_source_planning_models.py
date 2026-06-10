from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.source_planning.models import (
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
    IngestionPlan,
    IngestionStrategy,
    NormalisedTable,
    PreIngestBundle,
    RawTable,
    SourceFormat,
)
from groundworkers.source_planning.provenance import HeaderProvenance
from groundworkers.source_planning.warnings import PlanningError, PlanningWarning


def _raw_table() -> RawTable:
    return RawTable(
        name="demo",
        headers=["Code", "Label"],
        rows=[{"Code": 101, "Label": "Alpha"}],
        sample_rows=[{"Code": 101, "Label": "Alpha"}],
        source_format=SourceFormat.CSV,
        row_count=1,
        metadata={"sheet_index": 0},
    )


def _normalised_table() -> NormalisedTable:
    raw = _raw_table()
    return NormalisedTable.from_raw(
        raw,
        headers=["Code", "Label"],
        rows=[{"Code": "101", "Label": "Alpha"}],
        sample_rows=[{"Code": "101", "Label": "Alpha"}],
        header_provenance={
            "Code": HeaderProvenance(original="Code", normalised="Code", operations=[]),
            "Label": HeaderProvenance(original="Label", normalised="Label", operations=[]),
        },
        normalisation_notes=["mixed cell values were coerced to string surfaces"],
        warnings=[PlanningWarning(code="DEMO", message="demo warning", table_name="demo")],
    )


def test_raw_table_preserves_typed_pre_normalized_values():
    raw = _raw_table()
    assert raw.rows[0]["Code"] == 101
    assert isinstance(raw.rows[0]["Code"], int)


def test_normalised_table_retains_original_headers_and_provenance():
    table = _normalised_table()
    assert table.original_headers == ["Code", "Label"]
    assert table.headers == ["Code", "Label"]
    assert table.header_provenance["Code"].normalised == "Code"
    assert table.normalisation_notes == ["mixed cell values were coerced to string surfaces"]


def test_annotated_table_inherits_normalized_identity_and_semantic_fields():
    table = AnnotatedTable.from_normalised(
        _normalised_table(),
        column_annotations={
            "Code": ColumnAnnotation(role=ColumnRole.codes, detection_tier="A", confidence=0.98),
            "Label": ColumnAnnotation(role=ColumnRole.label, detection_tier="A", confidence=0.98),
        },
        classification_tier_used="A",
        classification_confidence=0.98,
        groundable_column_count=2,
        uncertain_columns=["Label"],
    )

    assert table.original_headers == ["Code", "Label"]
    assert table.code_columns() == ["Code"]
    assert table.label_columns() == ["Label"]
    assert table.is_groundable("Code") is True
    assert table.classification_confidence == 0.98
    assert table.uncertain_columns == ["Label"]


def test_ingestion_plan_groundable_tables_filters_unsupported_tables():
    supported = AnnotatedTable.from_normalised(
        _normalised_table(),
        column_annotations={"Code": ColumnAnnotation(role=ColumnRole.codes, detection_tier="A", confidence=0.9)},
        groundable_column_count=1,
        is_grounding_target=True,
    )
    unsupported = AnnotatedTable.from_normalised(
        _normalised_table(),
        is_grounding_target=False,
        non_target_reason="No groundable columns detected",
    )
    plan = IngestionPlan(
        format_detected=SourceFormat.CSV,
        tables=[supported, unsupported],
        strategies=[IngestionStrategy.DATA_DICT_IDEAL, IngestionStrategy.UNSUPPORTED],
        warnings=[PlanningWarning(code="WARN", message="warning")],
        errors=[PlanningError(code="ERR", message="error")],
    )

    assert plan.is_safe_to_ingest() is False
    assert plan.groundable_tables() == [(supported, IngestionStrategy.DATA_DICT_IDEAL)]


def test_pre_ingest_bundle_is_an_envelope_not_an_alias_for_plan():
    plan = IngestionPlan(format_detected=SourceFormat.CSV)
    bundle = PreIngestBundle(plan=plan, raw_tables=[_raw_table()])

    assert bundle.plan is plan
    assert bundle.raw_tables is not None
    assert bundle.normalised_tables is None
