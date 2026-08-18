

from groundworkers.services.source_planning.classifier import (
    ColumnRoleClassifier,
    classify_columns,
)
from groundworkers.services.source_planning.models import (
    ColumnRole,
    RawTable,
    SourceFormat,
)
from groundworkers.services.source_planning.normalisation import normalise_table


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


def test_classifier_marks_generic_code_label_and_infers_vocab_from_values():
    table = _normalised_table(
        headers=["Code", "Label"],
        rows=[{"Code": "E11.9", "Label": "Type 2 diabetes mellitus without complications"}],
    )

    annotated = classify_columns(table)

    assert annotated.column_annotations["Code"].role == ColumnRole.codes
    assert annotated.column_annotations["Code"].inferred_vocab == "ICD10CM"
    assert annotated.column_annotations["Code"].detection_tier == "C"
    assert annotated.column_annotations["Label"].role == ColumnRole.label
    assert annotated.groundable_column_count == 2
    assert annotated.is_grounding_target is True


def test_classifier_records_inferred_vocab_for_source_vocab_column():
    # A dedicated coding-system column naming an OMOP vocabulary must populate the
    # structured inferred_vocab field (not only free-text notes) so the router can
    # derive a domain hint from it.
    table = _normalised_table(
        headers=["Coding System", "Label"],
        rows=[
            {"Coding System": "LOINC", "Label": "Serum sodium"},
            {"Coding System": "LOINC", "Label": "Serum potassium"},
        ],
    )

    annotated = classify_columns(table)

    system = annotated.column_annotations["Coding System"]
    assert system.role == ColumnRole.source_vocab
    assert system.inferred_vocab == "LOINC"


def test_classifier_handles_uds_like_redcap_headers():
    table = _normalised_table(
        name="uds-v4-redcap-dd-04142026",
        headers=[
            "Variable / Field Name",
            "Form Name",
            "Section Header",
            "Field Type",
            "Field Label",
            "Choices, Calculations, OR Slider Labels",
            "Field Annotation",
        ],
        rows=[
            {
                "Variable / Field Name": "ptid",
                "Form Name": "form_header",
                "Section Header": "",
                "Field Type": "text",
                "Field Label": "PTID",
                "Choices, Calculations, OR Slider Labels": "",
                "Field Annotation": "@CHARLIMIT=10",
            },
            {
                "Variable / Field Name": "packet",
                "Form Name": "form_header",
                "Section Header": "Section 1 - Demographics",
                "Field Type": "radio",
                "Field Label": "Packet Code",
                "Choices, Calculations, OR Slider Labels": "I, Initial | F, Follow-up",
                "Field Annotation": "",
            },
        ],
    )

    annotated = ColumnRoleClassifier().classify(table)

    assert annotated.column_annotations["Variable / Field Name"].role == ColumnRole.attribute
    assert annotated.column_annotations["Form Name"].role == ColumnRole.section
    assert annotated.column_annotations["Section Header"].role == ColumnRole.section
    assert annotated.column_annotations["Field Label"].role == ColumnRole.label
    assert annotated.column_annotations["Choices, Calculations, OR Slider Labels"].role == ColumnRole.values
    assert annotated.column_annotations["Choices, Calculations, OR Slider Labels"].packed_value is True
    assert annotated.column_annotations["Field Annotation"].role == ColumnRole.annotation
    assert annotated.is_grounding_target is True
    assert annotated.groundable_column_count >= 3


def test_classifier_handles_untitled12_like_headers_and_pipeline_meta():
    table = _normalised_table(
        name="Untitled 12_2026-06-08-2354",
        headers=[
            "PROJECT_ID",
            "FIELD_NAME",
            "FORM_NAME",
            "ELEMENT_TYPE",
            "ELEMENT_LABEL",
            "ELEMENT_ENUM",
            "ELEMENT_NOTE",
            "_RAW_ELT_SOURCE",
        ],
        rows=[
            {
                "PROJECT_ID": "397",
                "FIELD_NAME": "em_prevautoenroll",
                "FORM_NAME": "autopsy_inclination",
                "ELEMENT_TYPE": "yesno",
                "ELEMENT_LABEL": "Enrolled for autopsy prior to this visit?",
                "ELEMENT_ENUM": "0, No | 1, Yes",
                "ELEMENT_NOTE": "If yes, save as complete",
                "_RAW_ELT_SOURCE": "metadata/redcap_metadata.csv",
            }
        ],
    )

    annotated = ColumnRoleClassifier().classify(table)

    assert annotated.column_annotations["FIELD_NAME"].role == ColumnRole.attribute
    assert annotated.column_annotations["FORM_NAME"].role == ColumnRole.section
    assert annotated.column_annotations["ELEMENT_TYPE"].role == ColumnRole.field_type_ctrl
    assert annotated.column_annotations["ELEMENT_LABEL"].role == ColumnRole.label
    assert annotated.column_annotations["ELEMENT_ENUM"].role == ColumnRole.values
    assert annotated.column_annotations["ELEMENT_NOTE"].role == ColumnRole.description
    assert annotated.column_annotations["_RAW_ELT_SOURCE"].role == ColumnRole.pipeline_meta
    assert "ELEMENT_ENUM" in annotated.packed_value_columns
    assert annotated.is_grounding_target is True


def test_classifier_uses_regex_fallback_and_surfaces_uncertainty():
    table = _normalised_table(
        headers=["Concept Identifier", "Question Name"],
        rows=[{"Concept Identifier": "ABC-001", "Question Name": "Primary diagnosis"}],
    )

    annotated = ColumnRoleClassifier().classify(table)

    assert annotated.column_annotations["Concept Identifier"].role == ColumnRole.codes
    assert annotated.column_annotations["Concept Identifier"].detection_tier == "B"
    assert "Concept Identifier" in annotated.uncertain_columns
    assert annotated.column_annotations["Question Name"].role == ColumnRole.label
    assert "Question Name" in annotated.uncertain_columns


def test_classifier_rejects_tables_with_no_groundable_columns():
    table = _normalised_table(
        name="metadata",
        headers=["_RAW_ELT_SOURCE", "FIELD_ORDER", "GRID_RANK"],
        rows=[
            {
                "_RAW_ELT_SOURCE": "metadata/redcap.csv",
                "FIELD_ORDER": "25",
                "GRID_RANK": "0",
            }
        ],
    )

    annotated = ColumnRoleClassifier().classify(table)

    assert annotated.is_grounding_target is False
    assert annotated.non_target_reason is not None
    assert annotated.groundable_column_count == 0
    assert annotated.classification_tier_used == "quick_reject"


def test_classifier_gate_rejects_generic_tables_with_no_groundable_columns():
    table = _normalised_table(
        name="demo",
        headers=["FIELD_ORDER", "GRID_RANK"],
        rows=[{"FIELD_ORDER": "25", "GRID_RANK": "0"}],
    )

    annotated = ColumnRoleClassifier().classify(table)

    assert annotated.is_grounding_target is False
    assert annotated.non_target_reason == "No groundable columns detected."
    assert annotated.groundable_column_count == 0
    assert annotated.classification_tier_used == "A"
