"""Neutral source-planning schemas.

The types in this module describe a strict pipeline:

``RawTable -> NormalisedTable -> AnnotatedTable -> IngestionPlan``

They are intentionally stateless. Nothing here depends on session ids,
database rows, review status, or ACP lifecycle state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from groundworkers.services.source_planning.provenance import HeaderProvenance
from groundworkers.services.source_planning.warnings import PlanningError, PlanningWarning


class SourceFormat(StrEnum):
    """Container format of the submitted source content."""

    CSV = "CSV"
    XLSX = "XLSX"
    XML = "XML"
    JSON = "JSON"
    DDL_SQL = "DDL_SQL"
    PDF = "PDF"
    DOCX = "DOCX"


class ColumnRole(StrEnum):
    """Semantic role assigned during column annotation."""

    label = "label"
    codes = "codes"
    values = "values"
    annotation = "annotation"

    entity = "entity"
    attribute = "attribute"
    section = "section"
    subsection = "subsection"
    source_vocab = "source_vocab"
    description = "description"
    mapping_context = "mapping_context"
    data_type = "data_type"
    field_type_ctrl = "field_type_ctrl"

    local_pk = "local_pk"
    pii_flag = "pii_flag"
    required = "required"
    frequency = "frequency"
    pipeline_meta = "pipeline_meta"
    irrelevant = "irrelevant"


_GROUNDABLE_ROLES = frozenset(
    {
        ColumnRole.label,
        ColumnRole.codes,
        ColumnRole.values,
        ColumnRole.annotation,
        ColumnRole.description,
        ColumnRole.source_vocab,
        ColumnRole.attribute,
    }
)


class IngestionStrategy(StrEnum):
    """Downstream ingestion strategy chosen from semantic annotation outputs."""

    DATA_DICT_IDEAL = "DATA_DICT_IDEAL"
    DATA_DICT_SCHEMA = "DATA_DICT_SCHEMA"
    DATA_DICT_PACKED_VALUES = "DATA_DICT_PACKED_VALUES"
    OWL_ONTOLOGY = "OWL_ONTOLOGY"
    FREE_TEXT_EXTRACT = "FREE_TEXT_EXTRACT"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(kw_only=True)
class ColumnAnnotation:
    """Semantic annotation for one normalized column."""

    role: ColumnRole
    detection_tier: Literal["A", "B", "C", "D", "LLM"]
    confidence: float
    inferred_vocab: str | None = None
    packed_value: bool = False
    notes: str = ""


@dataclass(kw_only=True)
class RawTable:
    """Post-decomposition table artifact before structural normalization.

    ``RawTable`` stays close to the extracted source shape. It may preserve
    typed or format-specific cell values and should not imply downstream
    semantic meaning.
    """

    name: str
    headers: list[str]
    rows: list[dict[str, Any]]
    sample_rows: list[dict[str, Any]]
    source_format: SourceFormat
    row_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class NormalisedTable:
    """Structural cleanup output for downstream semantic reasoning.

    This is the stage where header surfaces and cell text are stabilized.
    It must not assign column roles, domain hints, or strategy choices.
    """

    name: str
    headers: list[str]
    rows: list[dict[str, str]]
    sample_rows: list[dict[str, str]]
    source_format: SourceFormat
    row_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    original_headers: list[str] = field(default_factory=list)
    header_provenance: dict[str, HeaderProvenance] = field(default_factory=dict)
    normalisation_notes: list[str] = field(default_factory=list)
    warnings: list[PlanningWarning] = field(default_factory=list)

    @classmethod
    def from_raw(
        cls,
        table: RawTable,
        *,
        headers: list[str],
        rows: list[dict[str, str]],
        sample_rows: list[dict[str, str]],
        header_provenance: dict[str, HeaderProvenance],
        normalisation_notes: list[str] | None = None,
        warnings: list[PlanningWarning] | None = None,
    ) -> NormalisedTable:
        """Build a normalized table while preserving non-semantic identity."""

        return cls(
            name=table.name,
            headers=headers,
            rows=rows,
            sample_rows=sample_rows,
            source_format=table.source_format,
            row_count=table.row_count,
            metadata=dict(table.metadata),
            original_headers=list(table.headers),
            header_provenance=header_provenance,
            normalisation_notes=list(normalisation_notes or []),
            warnings=list(warnings or []),
        )


@dataclass(kw_only=True)
class AnnotatedTable(NormalisedTable):
    """Normalized table plus semantic annotation.

    This is the first stage that may infer downstream semantic intent. It should
    retain enough signal for caller-facing orchestration to decide whether the
    result is strong enough to accept, assist, or route for review.
    """

    column_annotations: dict[str, ColumnAnnotation] = field(default_factory=dict)
    packed_value_columns: list[str] = field(default_factory=list)
    classification_tier_used: Literal["A", "B", "C", "D", "LLM", "quick_reject"] = "A"
    classification_confidence: float | None = None
    uncertain_columns: list[str] = field(default_factory=list)
    llm_fallback_used: bool = False
    fallback_columns: list[str] = field(default_factory=list)
    groundable_column_count: int = 0
    is_grounding_target: bool = True
    non_target_reason: str | None = None
    domain_hint: str | None = None
    domain_hint_confidence: float | None = None
    annotation_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_normalised(cls, table: NormalisedTable, **kwargs: Any) -> AnnotatedTable:
        """Build semantic annotation output from a normalized table."""

        payload = dict(
            name=table.name,
            headers=list(table.headers),
            rows=list(table.rows),
            sample_rows=list(table.sample_rows),
            source_format=table.source_format,
            row_count=table.row_count,
            metadata=dict(table.metadata),
            original_headers=list(table.original_headers),
            header_provenance=dict(table.header_provenance),
            normalisation_notes=list(table.normalisation_notes),
            warnings=list(table.warnings),
        )
        payload.update(kwargs)
        return cls(**payload)

    def code_columns(self) -> list[str]:
        return [header for header, ann in self.column_annotations.items() if ann.role == ColumnRole.codes]

    def label_columns(self) -> list[str]:
        return [header for header, ann in self.column_annotations.items() if ann.role == ColumnRole.label]

    def section_column(self) -> str | None:
        for header, ann in self.column_annotations.items():
            if ann.role == ColumnRole.section:
                return header
        return None

    def vocab_column(self) -> str | None:
        for header, ann in self.column_annotations.items():
            if ann.role == ColumnRole.source_vocab:
                return header
        return None

    def values_columns(self) -> list[str]:
        return [header for header, ann in self.column_annotations.items() if ann.role == ColumnRole.values]

    def is_groundable(self, header: str) -> bool:
        ann = self.column_annotations.get(header)
        return ann is not None and ann.role in _GROUNDABLE_ROLES


@dataclass(kw_only=True)
class IngestionPlan:
    """Cross-table planning result consumed by stateful orchestration."""

    format_detected: SourceFormat
    caller_hint: str | None = None
    hint_matches: bool = True
    tables: list[AnnotatedTable] = field(default_factory=list)
    strategies: list[IngestionStrategy] = field(default_factory=list)
    warnings: list[PlanningWarning] = field(default_factory=list)
    errors: list[PlanningError] = field(default_factory=list)
    uncertain_tables: list[dict[str, Any]] = field(default_factory=list)

    def is_safe_to_ingest(self) -> bool:
        """Return ``True`` when the plan has no hard failures."""

        return not self.errors

    def groundable_tables(self) -> list[tuple[AnnotatedTable, IngestionStrategy]]:
        """Return tables that remain eligible for downstream ingestion."""

        return [
            (table, strategy)
            for table, strategy in zip(self.tables, self.strategies)
            if table.is_grounding_target and strategy != IngestionStrategy.UNSUPPORTED
        ]


@dataclass(kw_only=True)
class PreIngestBundle:
    """Top-level stateless planning result envelope.

    ``IngestionPlan`` remains the core decision object. The optional artifact
    lists exist for transparency, debugging, and adapter-facing inspection.
    """

    plan: IngestionPlan
    raw_tables: list[RawTable] | None = None
    normalised_tables: list[NormalisedTable] | None = None
    annotated_tables: list[AnnotatedTable] | None = None
    warnings: list[PlanningWarning] = field(default_factory=list)
    errors: list[PlanningError] = field(default_factory=list)
    elapsed_ms: int = 0
    llm_tier_used: bool = False
