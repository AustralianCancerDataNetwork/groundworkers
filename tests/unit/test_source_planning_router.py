from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.services.source_planning.classifier import ColumnRoleClassifier
from groundworkers.services.source_planning.models import (
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
    IngestionStrategy,
    RawTable,
    SourceFormat,
)
from groundworkers.services.source_planning.normalisation import normalise_table
from groundworkers.services.source_planning.router import IngesterRouter, route_table


def _normalised_table(
    *,
    headers: list[str],
    rows: list[dict[str, object]],
    name: str = "demo",
    source_format: SourceFormat = SourceFormat.CSV,
    metadata: dict[str, object] | None = None,
):
    raw = RawTable(
        name=name,
        headers=headers,
        rows=rows,
        sample_rows=rows[:5],
        source_format=source_format,
        row_count=len(rows),
        metadata=metadata or {},
    )
    return normalise_table(raw)


def _annotated_table(
    *,
    headers: list[str],
    rows: list[dict[str, object]],
    annotations: dict[str, ColumnAnnotation],
    name: str = "demo",
    classification_confidence: float | None = 0.95,
    uncertain_columns: list[str] | None = None,
    is_grounding_target: bool = True,
    non_target_reason: str | None = None,
) -> AnnotatedTable:
    table = _normalised_table(headers=headers, rows=rows, name=name)
    packed_value_columns = [
        header for header, annotation in annotations.items() if annotation.packed_value
    ]
    groundable_count = sum(
        1
        for annotation in annotations.values()
        if annotation.role
        in {
            ColumnRole.label,
            ColumnRole.codes,
            ColumnRole.values,
            ColumnRole.annotation,
            ColumnRole.description,
            ColumnRole.source_vocab,
            ColumnRole.attribute,
        }
    )
    return AnnotatedTable.from_normalised(
        table,
        column_annotations=annotations,
        packed_value_columns=packed_value_columns,
        classification_tier_used="A",
        classification_confidence=classification_confidence,
        uncertain_columns=list(uncertain_columns or []),
        groundable_column_count=groundable_count,
        is_grounding_target=is_grounding_target,
        non_target_reason=non_target_reason,
    )


def test_router_assigns_data_dict_ideal_and_domain_hint():
    table = _annotated_table(
        headers=["Code", "Label"],
        rows=[{"Code": "E11.9", "Label": "Type 2 diabetes mellitus"}],
        annotations={
            "Code": ColumnAnnotation(
                role=ColumnRole.codes,
                detection_tier="C",
                confidence=0.9,
                inferred_vocab="ICD10CM",
            ),
            "Label": ColumnAnnotation(role=ColumnRole.label, detection_tier="A", confidence=1.0),
        },
    )

    strategy, routed = route_table(table)

    assert strategy == IngestionStrategy.DATA_DICT_IDEAL
    assert routed.domain_hint == "Condition"
    assert routed.domain_hint_confidence == 0.9


def test_router_assigns_packed_values_strategy():
    table = _annotated_table(
        headers=["Field Label", "Choices"],
        rows=[{"Field Label": "Packet Code", "Choices": "I, Initial | F, Follow-up"}],
        annotations={
            "Field Label": ColumnAnnotation(role=ColumnRole.label, detection_tier="A", confidence=1.0),
            "Choices": ColumnAnnotation(
                role=ColumnRole.values,
                detection_tier="A",
                confidence=1.0,
                packed_value=True,
            ),
        },
    )

    strategy, routed = route_table(table)

    assert strategy == IngestionStrategy.DATA_DICT_PACKED_VALUES
    assert any(w.code == "PACKED_VALUES_ROUTE" for w in routed.warnings)


def test_router_assigns_schema_strategy_for_label_attribute_tables():
    table = _annotated_table(
        headers=["Field Name", "Field Label"],
        rows=[{"Field Name": "ptid", "Field Label": "Participant ID"}],
        annotations={
            "Field Name": ColumnAnnotation(role=ColumnRole.attribute, detection_tier="A", confidence=1.0),
            "Field Label": ColumnAnnotation(role=ColumnRole.label, detection_tier="A", confidence=1.0),
        },
    )

    strategy, routed = route_table(table)

    assert strategy == IngestionStrategy.DATA_DICT_SCHEMA
    assert routed.is_grounding_target is True


def test_router_preserves_non_target_as_unsupported():
    table = _annotated_table(
        headers=["_RAW_ELT_SOURCE"],
        rows=[{"_RAW_ELT_SOURCE": "metadata.csv"}],
        annotations={
            "_RAW_ELT_SOURCE": ColumnAnnotation(
                role=ColumnRole.pipeline_meta,
                detection_tier="A",
                confidence=1.0,
            )
        },
        is_grounding_target=False,
        non_target_reason="Table name 'metadata' is a known non-target.",
    )

    strategy, routed = route_table(table)

    assert strategy == IngestionStrategy.UNSUPPORTED
    assert routed.is_grounding_target is False
    assert any(w.code == "UNSUPPORTED_NON_TARGET" for w in routed.warnings)


def test_router_marks_unroutable_grounding_target_as_unsupported():
    table = _annotated_table(
        headers=["Annotation Only"],
        rows=[{"Annotation Only": "CURIE:123"}],
        annotations={
            "Annotation Only": ColumnAnnotation(
                role=ColumnRole.annotation,
                detection_tier="B",
                confidence=0.75,
            )
        },
        classification_confidence=0.75,
    )

    strategy, routed = route_table(table)

    assert strategy == IngestionStrategy.UNSUPPORTED
    assert routed.is_grounding_target is False
    assert routed.non_target_reason == "No supported ingestion strategy matched the annotated table."
    assert any(w.code == "UNSUPPORTED_NO_ROUTE" for w in routed.warnings)


def test_router_warns_when_columns_remain_uncertain():
    table = _annotated_table(
        headers=["Concept Identifier", "Question Name"],
        rows=[{"Concept Identifier": "ABC-001", "Question Name": "Primary diagnosis"}],
        annotations={
            "Concept Identifier": ColumnAnnotation(role=ColumnRole.codes, detection_tier="B", confidence=0.75),
            "Question Name": ColumnAnnotation(role=ColumnRole.label, detection_tier="B", confidence=0.75),
        },
        classification_confidence=0.75,
        uncertain_columns=["Concept Identifier", "Question Name"],
    )

    strategy, routed = route_table(table)

    assert strategy == IngestionStrategy.DATA_DICT_IDEAL
    assert any(w.code == "UNCERTAIN_COLUMNS" for w in routed.warnings)


def test_router_integration_routes_uds_like_table_to_packed_values():
    normalised = _normalised_table(
        name="uds-v4-redcap-dd-04142026",
        headers=[
            "Variable / Field Name",
            "Form Name",
            "Field Type",
            "Field Label",
            "Choices, Calculations, OR Slider Labels",
        ],
        rows=[
            {
                "Variable / Field Name": "packet",
                "Form Name": "form_header",
                "Field Type": "radio",
                "Field Label": "Packet Code",
                "Choices, Calculations, OR Slider Labels": "I, Initial | F, Follow-up",
            }
        ],
    )

    annotated = ColumnRoleClassifier().classify(normalised)
    strategy, routed = IngesterRouter().route(annotated)

    assert strategy == IngestionStrategy.DATA_DICT_PACKED_VALUES
    assert routed.is_grounding_target is True


def test_router_integration_routes_untitled12_like_table_to_packed_values():
    normalised = _normalised_table(
        name="Untitled 12_2026-06-08-2354",
        headers=[
            "FIELD_NAME",
            "FORM_NAME",
            "ELEMENT_TYPE",
            "ELEMENT_LABEL",
            "ELEMENT_ENUM",
            "ELEMENT_NOTE",
        ],
        rows=[
            {
                "FIELD_NAME": "em_prevautoenroll",
                "FORM_NAME": "autopsy_inclination",
                "ELEMENT_TYPE": "yesno",
                "ELEMENT_LABEL": "Enrolled for autopsy prior to this visit?",
                "ELEMENT_ENUM": "0, No | 1, Yes",
                "ELEMENT_NOTE": "If yes, save as complete",
            }
        ],
    )

    annotated = ColumnRoleClassifier().classify(normalised)
    strategy, routed = IngesterRouter().route(annotated)

    assert strategy == IngestionStrategy.DATA_DICT_PACKED_VALUES
    assert routed.is_grounding_target is True
