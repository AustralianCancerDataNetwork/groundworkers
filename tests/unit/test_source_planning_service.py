from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.services.source_planning.models import (
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
    IngestionStrategy,
    NormalisedTable,
    RawTable,
    SourceFormat,
)
from groundworkers.services.source_planning.provenance import HeaderProvenance
from groundworkers.services.source_planning.service import (
    SourcePlanningService,
    plan_source,
)
from groundworkers.services.source_planning.assisted import AssistedColumnRoleClassifier


def _raw_table(
    *,
    name: str = "demo",
    headers: list[str],
    rows: list[dict[str, object]],
    source_format: SourceFormat = SourceFormat.CSV,
) -> RawTable:
    return RawTable(
        name=name,
        headers=headers,
        rows=rows,
        sample_rows=rows[:5],
        source_format=source_format,
        row_count=len(rows),
    )


def test_plan_tables_builds_bundle_and_routes_ideal():
    service = SourcePlanningService()
    bundle = service.plan_tables(
        [
            _raw_table(
                headers=["Code", "Label"],
                rows=[{"Code": "E11.9", "Label": "Type 2 diabetes mellitus"}],
            )
        ]
    )

    assert bundle.plan.format_detected == SourceFormat.CSV
    assert bundle.plan.strategies == [IngestionStrategy.DATA_DICT_IDEAL]
    assert bundle.raw_tables is not None and len(bundle.raw_tables) == 1
    assert bundle.normalised_tables is not None and len(bundle.normalised_tables) == 1
    assert bundle.annotated_tables is not None and len(bundle.annotated_tables) == 1
    assert bundle.plan.tables[0].domain_hint == "Condition"


def test_plan_source_detects_decomposes_and_routes_csv():
    bundle = plan_source(
        "code,label\nE11.9,Type 2 diabetes mellitus\n",
        filename="diagnoses.csv",
    )

    assert bundle.plan.format_detected == SourceFormat.CSV
    assert bundle.plan.strategies == [IngestionStrategy.DATA_DICT_IDEAL]
    assert bundle.raw_tables is not None
    assert bundle.raw_tables[0].name == "diagnoses"


def test_plan_source_routes_uds_like_redcap_csv_to_packed_values():
    content = (
        '"Variable / Field Name","Form Name","Field Type","Field Label","Choices, Calculations, OR Slider Labels"\n'
        'ptid,form_header,text,PTID,\n'
        'packet,form_header,radio,Packet Code,"I, Initial | F, Follow-up"\n'
    )

    bundle = plan_source(content, filename="uds-v4-redcap-dd-04142026.csv", caller_hint="data_dict")

    assert bundle.plan.strategies == [IngestionStrategy.DATA_DICT_PACKED_VALUES]
    assert bundle.plan.hint_matches is True


def test_plan_source_routes_untitled12_like_csv_to_packed_values():
    content = (
        "PROJECT_ID,FIELD_NAME,FORM_NAME,ELEMENT_TYPE,ELEMENT_LABEL,ELEMENT_ENUM,ELEMENT_NOTE,_RAW_ELT_SOURCE\n"
        "397,em_prevautoenroll,autopsy_inclination,yesno,Enrolled for autopsy prior to this visit?,\"0, No | 1, Yes\",If yes save,metadata/redcap.csv\n"
    )

    bundle = plan_source(content, filename="Untitled 12_2026-06-08-2354.csv", caller_hint="redcap")

    assert bundle.plan.strategies == [IngestionStrategy.DATA_DICT_PACKED_VALUES]
    assert bundle.plan.tables[0].is_grounding_target is True
    assert bundle.plan.hint_matches is True


def test_plan_source_records_hint_mismatch_without_failing():
    bundle = plan_source(
        "code,label\nA,Alpha\n",
        filename="demo.csv",
        caller_hint="json",
    )

    assert bundle.plan.hint_matches is False
    assert bundle.plan.errors == []
    assert any(w.code == "HINT_MISMATCH" for w in bundle.plan.warnings)


def test_plan_tables_populates_uncertain_tables():
    service = SourcePlanningService(
        detector=StubDetector(SourceFormat.CSV),
        decomposer=StubDecomposer([]),
        classifier=StubClassifier(
            AnnotatedTable(
                name="demo",
                headers=["Question Name"],
                rows=[{"Question Name": "Primary diagnosis"}],
                sample_rows=[{"Question Name": "Primary diagnosis"}],
                source_format=SourceFormat.CSV,
                row_count=1,
                metadata={},
                original_headers=["Question Name"],
                header_provenance={
                    "Question Name": HeaderProvenance(
                        original="Question Name",
                        normalised="Question Name",
                        operations=[],
                    )
                },
                normalisation_notes=[],
                warnings=[],
                column_annotations={
                    "Question Name": ColumnAnnotation(
                        role=ColumnRole.label,
                        detection_tier="B",
                        confidence=0.75,
                    )
                },
                classification_tier_used="B",
                classification_confidence=0.75,
                uncertain_columns=["Question Name"],
                groundable_column_count=1,
            )
        ),
        router=StubRouter(IngestionStrategy.DATA_DICT_SCHEMA),
    )

    bundle = service.plan_tables(
        [_raw_table(headers=["Question Name"], rows=[{"Question Name": "Primary diagnosis"}])]
    )

    assert bundle.plan.uncertain_tables
    assert bundle.plan.uncertain_tables[0]["table_name"] == "demo"
    assert bundle.plan.uncertain_tables[0]["proposed_strategy"] == "DATA_DICT_SCHEMA"


def test_plan_source_uses_injected_dependencies_in_order():
    events: list[str] = []
    raw = _raw_table(headers=["Code", "Label"], rows=[{"Code": "A", "Label": "Alpha"}])
    annotated = AnnotatedTable(
        name="demo",
        headers=["Code", "Label"],
        rows=[{"Code": "A", "Label": "Alpha"}],
        sample_rows=[{"Code": "A", "Label": "Alpha"}],
        source_format=SourceFormat.CSV,
        row_count=1,
        metadata={},
        original_headers=["Code", "Label"],
        header_provenance={
            "Code": HeaderProvenance(original="Code", normalised="Code", operations=[]),
            "Label": HeaderProvenance(original="Label", normalised="Label", operations=[]),
        },
        normalisation_notes=[],
        warnings=[],
        column_annotations={
            "Code": ColumnAnnotation(role=ColumnRole.codes, detection_tier="A", confidence=1.0),
            "Label": ColumnAnnotation(role=ColumnRole.label, detection_tier="A", confidence=1.0),
        },
        groundable_column_count=2,
    )
    service = SourcePlanningService(
        detector=RecordingDetector(events, SourceFormat.CSV),
        decomposer=RecordingDecomposer(events, [raw]),
        classifier=RecordingClassifier(events, annotated),
        router=RecordingRouter(events, IngestionStrategy.DATA_DICT_IDEAL),
    )

    bundle = service.plan_source("Code,Label\nA,Alpha\n", filename="demo.csv")

    assert [event.split(":")[0] for event in events] == ["detect", "decompose", "classify", "route"]
    assert bundle.plan.strategies == [IngestionStrategy.DATA_DICT_IDEAL]


def test_plan_tables_assisted_marks_fallback_provenance():
    service = SourcePlanningService(
        classifier=StubClassifier(
            AnnotatedTable(
                name="demo",
                headers=["Question Name"],
                rows=[{"Question Name": "Primary diagnosis"}],
                sample_rows=[{"Question Name": "Primary diagnosis"}],
                source_format=SourceFormat.CSV,
                row_count=1,
                metadata={},
                original_headers=["Question Name"],
                header_provenance={
                    "Question Name": HeaderProvenance(
                        original="Question Name",
                        normalised="Question Name",
                        operations=[],
                    )
                },
                normalisation_notes=[],
                warnings=[],
                column_annotations={
                    "Question Name": ColumnAnnotation(
                        role=ColumnRole.label,
                        detection_tier="B",
                        confidence=0.75,
                    )
                },
                classification_tier_used="B",
                classification_confidence=0.75,
                uncertain_columns=["Question Name"],
                groundable_column_count=1,
            )
        ),
        assisted_classifier=StubAssistedClassifier(),
        router=StubRouter(IngestionStrategy.DATA_DICT_SCHEMA),
    )

    bundle = service.plan_tables_assisted(
        [_raw_table(headers=["Question Name"], rows=[{"Question Name": "Primary diagnosis"}])]
    )

    table = bundle.plan.tables[0]
    assert table.llm_fallback_used is True
    assert table.classification_tier_used == "LLM"
    assert table.fallback_columns == ["Question Name"]
    assert table.uncertain_columns == []


class StubDetector:
    def __init__(self, source_format: SourceFormat) -> None:
        self._source_format = source_format

    def detect(self, content: bytes, filename: str | None = None) -> SourceFormat:
        return self._source_format


class StubDecomposer:
    def __init__(self, tables: list[RawTable]) -> None:
        self._tables = tables

    def decompose(
        self,
        content: bytes,
        source_format: SourceFormat,
        filename: str | None = None,
    ) -> list[RawTable]:
        return self._tables


class StubClassifier:
    def __init__(self, annotated: AnnotatedTable) -> None:
        self._annotated = annotated

    def classify(self, table: NormalisedTable) -> AnnotatedTable:
        return self._annotated


class StubRouter:
    def __init__(self, strategy: IngestionStrategy) -> None:
        self._strategy = strategy

    def route(self, table: AnnotatedTable) -> tuple[IngestionStrategy, AnnotatedTable]:
        return self._strategy, table


class StubAssistedClassifier:
    def classify(self, *, baseline: AnnotatedTable, model_name: str | None = None) -> AnnotatedTable:
        return baseline.__class__.from_normalised(
            baseline,
            column_annotations={
                "Question Name": ColumnAnnotation(
                    role=ColumnRole.label,
                    detection_tier="LLM",
                    confidence=0.95,
                )
            },
            classification_tier_used="LLM",
            classification_confidence=0.95,
            uncertain_columns=[],
            llm_fallback_used=True,
            fallback_columns=["Question Name"],
            groundable_column_count=1,
        )


class RecordingDetector:
    def __init__(self, events: list[str], source_format: SourceFormat) -> None:
        self._events = events
        self._source_format = source_format

    def detect(self, content: bytes, filename: str | None = None) -> SourceFormat:
        self._events.append(f"detect:{filename}")
        return self._source_format


class RecordingDecomposer:
    def __init__(self, events: list[str], tables: list[RawTable]) -> None:
        self._events = events
        self._tables = tables

    def decompose(
        self,
        content: bytes,
        source_format: SourceFormat,
        filename: str | None = None,
    ) -> list[RawTable]:
        self._events.append(f"decompose:{source_format.value}")
        return self._tables


class RecordingClassifier:
    def __init__(self, events: list[str], annotated: AnnotatedTable) -> None:
        self._events = events
        self._annotated = annotated

    def classify(self, table: NormalisedTable) -> AnnotatedTable:
        self._events.append(f"classify:{table.name}")
        return self._annotated


class RecordingRouter:
    def __init__(self, events: list[str], strategy: IngestionStrategy) -> None:
        self._events = events
        self._strategy = strategy

    def route(self, table: AnnotatedTable) -> tuple[IngestionStrategy, AnnotatedTable]:
        self._events.append(f"route:{table.name}")
        return self._strategy, table
