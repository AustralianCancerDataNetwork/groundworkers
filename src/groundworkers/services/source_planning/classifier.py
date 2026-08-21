"""Deterministic column classification for normalized source tables."""

from __future__ import annotations

import re

from groundworkers.services.source_planning.canonical_headers import (
    lookup as lookup_canonical_header,
)
from groundworkers.services.source_planning.models import (
    _GROUNDABLE_ROLES,
    UNCERTAIN_CONFIDENCE_THRESHOLD,
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
    DetectionTier,
    NormalisedTable,
    SourceFormat,
)
from groundworkers.services.source_planning.warnings import PlanningWarning

_NON_TARGET_TABLE_NAMES: frozenset[str] = frozenset(
    {
        "contents",
        "content",
        "table of contents",
        "toc",
        "metadata",
        "meta",
        "readme",
        "read me",
        "cover",
        "overview",
        "instructions",
        "about",
        "legend",
        "notes",
        "change log",
        "changelog",
        "revision history",
        "version history",
        "abstract",
        "references",
        "bibliography",
        "appendix",
        "contact",
        "contacts",
    }
)

_XLSX_POSITIONAL_SHEET_THRESHOLD = 3
_XLSX_GROUNDABLE_RATIO_THRESHOLD = 0.15
_RE_CODES = re.compile(r"\bcode\b|\bidentifier\b", re.IGNORECASE)
_RE_CODES_EXCLUDE = re.compile(r"system|vocab|vocabulary|\?$", re.IGNORECASE)
_RE_LABEL = re.compile(r"\bname\b|\blabel\b|\bquestion\b|\btitle\b", re.IGNORECASE)
_RE_LABEL_EXCLUDE = re.compile(r"table_name|file_name|form_name", re.IGNORECASE)
_RE_DESCRIPTION = re.compile(r"\bdescription\b|\bnote\b|\bcomment\b|\bhelp\b", re.IGNORECASE)
_RE_SECTION = re.compile(r"\bsection\b|\bgroup\b|\bcategory\b|\bform\b|\bmodule\b|\btable\b", re.IGNORECASE)
_RE_VOCAB = re.compile(r"\bsystem\b|\bvocab\b|\bvocabulary\b|\bcoding\b", re.IGNORECASE)
_RE_VOCAB_EXCLUDE = re.compile(r"^_")
_RE_VALUES = re.compile(r"\bvalues?\b|\bchoices?\b|\benums?\b|\boptions?\b|\ballowed\b", re.IGNORECASE)
_RE_ANNOT = re.compile(r"\bcui\b|\buri\b|\bcurie?\b|\bxref\b|\bcross.?ref\b", re.IGNORECASE)
_RE_LOCAL_PK = re.compile(r"_id$|^id$|\bid\b", re.IGNORECASE)
_CAMEL_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])")

_VOCAB_NAME_ALIASES: dict[str, str] = {
    "snomed": "SNOMED",
    "snomed ct": "SNOMED",
    "loinc": "LOINC",
    "icd10": "ICD10CM",
    "icd10cm": "ICD10CM",
    "icd 10 cm": "ICD10CM",
    "icd-10-cm": "ICD10CM",
    "icd9": "ICD9CM",
    "icd9cm": "ICD9CM",
    "rxnorm": "RxNorm",
    "ndc": "NDC",
    "hcpcs": "HCPCS",
    "cpt": "CPT4",
    "cpt4": "CPT4",
    "drg": "DRG",
    "atc": "ATC",
}

_VAGUE_VOCAB_NAMES = tuple(re.escape(name) for name in _VOCAB_NAME_ALIASES)
_VOCAB_RE = re.compile("|".join(_VAGUE_VOCAB_NAMES), re.IGNORECASE)

_CODE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^[A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?$", re.IGNORECASE), "ICD10CM"),
    (re.compile(r"^\d{1,6}-\d{1,2}$"), "LOINC"),
)

class ColumnRoleClassifier:
    """Classify normalized columns using deterministic header and sample rules.

    The classifier is intentionally conservative. It should assign obvious
    semantic roles confidently and leave weaker cases visible as uncertain
    instead of overfitting to one source-system shape.
    """

    def classify(self, table: NormalisedTable) -> AnnotatedTable:
        """Return semantic annotations for one normalized table."""

        if self._is_non_target_name(table.name):
            return AnnotatedTable.from_normalised(
                table,
                classification_tier_used="quick_reject",
                classification_confidence=0.0,
                is_grounding_target=False,
                non_target_reason=f"Table name {table.name!r} is a known non-target.",
                annotation_notes=["table rejected by non-target name check"],
                warnings=[
                    *table.warnings,
                    PlanningWarning(
                        code="NON_TARGET_TABLE",
                        message=f"Table {table.name!r} was rejected by name.",
                        table_name=table.name,
                    )
                ],
            )

        if self._is_xlsx_cover_sheet(table):
            return AnnotatedTable.from_normalised(
                table,
                classification_tier_used="quick_reject",
                classification_confidence=0.0,
                is_grounding_target=False,
                non_target_reason="Workbook cover sheet rejected by positional heuristic.",
                annotation_notes=["table rejected by xlsx cover-sheet heuristic"],
                warnings=[
                    *table.warnings,
                    PlanningWarning(
                        code="XLSX_COVER_SHEET_REJECTED",
                        message="Workbook cover sheet had too few groundable columns.",
                        table_name=table.name,
                    )
                ],
            )

        column_annotations: dict[str, ColumnAnnotation] = {}
        packed_value_columns: list[str] = []
        uncertain_columns: list[str] = []
        notes: list[str] = []
        tier_used = "A"

        for header in table.headers:
            ann = self._classify_column(header, table.sample_rows)
            if ann is None:
                continue
            column_annotations[header] = ann
            if ann.packed_value:
                packed_value_columns.append(header)
            if ann.confidence < UNCERTAIN_CONFIDENCE_THRESHOLD:
                uncertain_columns.append(header)
            tier_used = _max_tier(tier_used, ann.detection_tier)

        groundable_count = sum(1 for ann in column_annotations.values() if ann.role in _GROUNDABLE_ROLES)
        warnings = list(table.warnings)

        if groundable_count == 0:
            warnings.append(
                PlanningWarning(
                    code="NO_GROUNDABLE_COLUMNS",
                    message="No groundable columns were detected during deterministic classification.",
                    table_name=table.name,
                )
            )
            return AnnotatedTable.from_normalised(
                table,
                column_annotations=column_annotations,
                packed_value_columns=packed_value_columns,
                classification_tier_used=tier_used,
                classification_confidence=_overall_confidence(column_annotations),
                uncertain_columns=uncertain_columns,
                groundable_column_count=0,
                is_grounding_target=False,
                non_target_reason="No groundable columns detected.",
                annotation_notes=["deterministic classifier found no groundable columns"],
                warnings=warnings,
            )

        if uncertain_columns:
            notes.append("some column roles were assigned conservatively and remain candidate review points")
        if packed_value_columns:
            notes.append("packed-value columns were identified for downstream set expansion")

        return AnnotatedTable.from_normalised(
            table,
            column_annotations=column_annotations,
            packed_value_columns=packed_value_columns,
            classification_tier_used=tier_used,
            classification_confidence=_overall_confidence(column_annotations),
            uncertain_columns=uncertain_columns,
            groundable_column_count=groundable_count,
            annotation_notes=notes,
            warnings=warnings,
        )

    def classify_tables(self, tables: list[NormalisedTable]) -> list[AnnotatedTable]:
        """Classify multiple normalized tables."""

        return [self.classify(table) for table in tables]

    def _classify_column(
        self,
        header: str,
        sample_rows: list[dict[str, str]],
    ) -> ColumnAnnotation | None:
        entry = lookup_canonical_header(header)
        if entry is not None:
            role, confidence, inferred_vocab, packed_value = entry
            annotation = ColumnAnnotation(
                role=role,
                detection_tier="A",
                confidence=confidence,
                inferred_vocab=inferred_vocab,
                packed_value=packed_value,
            )
            return self._enrich_from_samples(header, annotation, sample_rows)

        annotation = self._tier_b(header)
        if annotation is None:
            return None
        return self._enrich_from_samples(header, annotation, sample_rows)

    def _tier_b(self, header: str) -> ColumnAnnotation | None:
        h_raw = header.strip()
        h = _CAMEL_SPLIT.sub(" ", h_raw).replace(".", " ").replace("@", " ").replace("/", " ").lower()

        if _RE_CODES.search(h) and not _RE_CODES_EXCLUDE.search(h):
            if _VOCAB_RE.search(h):
                return ColumnAnnotation(
                    role=ColumnRole.source_vocab,
                    detection_tier="B",
                    confidence=0.8,
                    notes="header combines code and vocabulary cues",
                )
            return ColumnAnnotation(role=ColumnRole.codes, detection_tier="B", confidence=0.75)

        if _RE_VOCAB.search(h) and not _RE_VOCAB_EXCLUDE.search(h_raw):
            return ColumnAnnotation(role=ColumnRole.source_vocab, detection_tier="B", confidence=0.8)

        if _VOCAB_RE.fullmatch(h):
            return ColumnAnnotation(
                role=ColumnRole.codes,
                detection_tier="B",
                confidence=0.7,
                notes="header matched a known vocabulary name",
            )

        if _RE_VALUES.search(h):
            return ColumnAnnotation(role=ColumnRole.values, detection_tier="B", confidence=0.75, packed_value=True)

        if _RE_LABEL.search(h) and not _RE_LABEL_EXCLUDE.search(h):
            return ColumnAnnotation(role=ColumnRole.label, detection_tier="B", confidence=0.75)

        if _RE_DESCRIPTION.search(h):
            return ColumnAnnotation(role=ColumnRole.description, detection_tier="B", confidence=0.75)

        if _RE_SECTION.search(h):
            return ColumnAnnotation(role=ColumnRole.section, detection_tier="B", confidence=0.7)

        if _RE_ANNOT.search(h):
            return ColumnAnnotation(role=ColumnRole.annotation, detection_tier="B", confidence=0.7)

        if _RE_LOCAL_PK.search(h):
            return ColumnAnnotation(role=ColumnRole.local_pk, detection_tier="B", confidence=0.7)

        return None

    def _enrich_from_samples(
        self,
        header: str,
        annotation: ColumnAnnotation,
        sample_rows: list[dict[str, str]],
    ) -> ColumnAnnotation:
        sample_values = [str(row.get(header, "")).strip() for row in sample_rows if str(row.get(header, "")).strip()]
        if not sample_values:
            return annotation

        inferred_vocab = annotation.inferred_vocab
        notes = annotation.notes
        packed_value = annotation.packed_value
        detection_tier = annotation.detection_tier
        confidence = annotation.confidence

        if annotation.role == ColumnRole.codes and inferred_vocab is None:
            vocab_from_values = _infer_vocab_from_values(sample_values)
            if vocab_from_values is not None:
                inferred_vocab = vocab_from_values
                detection_tier = _max_tier(detection_tier, "C")
                confidence = max(confidence, 0.82)
                notes = _merge_notes(notes, "vocabulary inferred from sample code format")

        if annotation.role == ColumnRole.source_vocab and inferred_vocab is None:
            vocab_from_values = _infer_vocab_name(sample_values)
            if vocab_from_values is not None:
                # Record the resolved vocabulary in the structured field, not only in
                # notes — the router derives table-level domain hints from
                # inferred_vocab, so a source-vocab column naming e.g. LOINC must
                # contribute that signal rather than being lost to free text.
                inferred_vocab = vocab_from_values
                detection_tier = _max_tier(detection_tier, "C")
                confidence = max(confidence, 0.82)
                notes = _merge_notes(notes, f"source vocabulary column resolves to OMOP vocabulary {vocab_from_values}")

        if annotation.role == ColumnRole.values and not packed_value and _looks_packed(sample_values):
            packed_value = True
            detection_tier = _max_tier(detection_tier, "C")
            confidence = max(confidence, 0.82)
            notes = _merge_notes(notes, "packed values detected from sample rows")

        return ColumnAnnotation(
            role=annotation.role,
            detection_tier=detection_tier,
            confidence=confidence,
            inferred_vocab=inferred_vocab,
            packed_value=packed_value,
            notes=notes,
        )

    @staticmethod
    def _is_non_target_name(name: str) -> bool:
        return name.strip().lower() in _NON_TARGET_TABLE_NAMES

    @staticmethod
    def _is_xlsx_cover_sheet(table: NormalisedTable) -> bool:
        if table.source_format != SourceFormat.XLSX:
            return False
        sheet_index = table.metadata.get("sheet_index", 0)
        sheet_count = table.metadata.get("sheet_count", 1)
        if sheet_index != 0 or sheet_count < _XLSX_POSITIONAL_SHEET_THRESHOLD:
            return False
        total_cols = len(table.headers)
        if total_cols == 0:
            return True
        groundable = sum(1 for header in table.headers if _is_tier_a_groundable(header))
        ratio = groundable / total_cols
        return ratio < _XLSX_GROUNDABLE_RATIO_THRESHOLD


def classify_columns(table: NormalisedTable) -> AnnotatedTable:
    """Convenience wrapper for one-shot deterministic classification."""

    return ColumnRoleClassifier().classify(table)


def classify_tables(tables: list[NormalisedTable]) -> list[AnnotatedTable]:
    """Convenience wrapper for one-shot classification over many tables."""

    return ColumnRoleClassifier().classify_tables(tables)


def _infer_vocab_name(values: list[str]) -> str | None:
    normalized = {_normalize_vocab_name(value) for value in values if _normalize_vocab_name(value)}
    if len(normalized) == 1:
        return next(iter(normalized))
    return None


def _infer_vocab_from_values(values: list[str]) -> str | None:
    matched: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        alias = _normalize_vocab_name(cleaned)
        if alias is not None:
            matched.add(alias)
            continue
        for pattern, vocab in _CODE_PATTERNS:
            if pattern.fullmatch(cleaned):
                matched.add(vocab)
                break
    if len(matched) == 1:
        return next(iter(matched))
    return None


def _normalize_vocab_name(value: str) -> str | None:
    cleaned = re.sub(r"[\s_\-]+", " ", value.strip().lower())
    return _VOCAB_NAME_ALIASES.get(cleaned)


def _looks_packed(values: list[str]) -> bool:
    delimiters = ("|", ";", "\n")
    return any(any(delimiter in value for delimiter in delimiters) for value in values)


def _overall_confidence(column_annotations: dict[str, ColumnAnnotation]) -> float | None:
    if not column_annotations:
        return None
    return round(
        sum(annotation.confidence for annotation in column_annotations.values()) / len(column_annotations),
        3,
    )


def _max_tier(left: DetectionTier, right: DetectionTier) -> DetectionTier:
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "LLM": 4}
    return left if order[left] >= order[right] else right


def _merge_notes(existing: str, addition: str) -> str:
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing}; {addition}"


def _is_tier_a_groundable(header: str) -> bool:
    entry = lookup_canonical_header(header)
    if entry is None:
        return False
    return entry[0] in _GROUNDABLE_ROLES
