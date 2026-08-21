"""Explicit LLM-assisted source-planning classification.

The assisted path is separate from deterministic classification on purpose.
Caller-facing orchestration decides when a deterministic result is not strong
enough and explicitly requests this fallback path.
"""

from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel, Field, ValidationError

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.models import (
    _GROUNDABLE_ROLES,
    COLUMN_ROLE_DESCRIPTIONS,
    UNCERTAIN_CONFIDENCE_THRESHOLD,
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
)

_SYSTEM_PROMPT = """\
You assist with source-planning column classification for OMOP-grounding-adjacent files.
Review the table context and propose roles only for the candidate columns supplied.
Be conservative. Prefer leaving a role unchanged if the evidence is weak.
Respond with raw JSON only.
"""


class AssistedColumnDecision(BaseModel):
    header: str
    role: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    inferred_vocab: str | None = None
    packed_value: bool = False
    notes: str = ""


class AssistedClassificationResult(BaseModel):
    decisions: list[AssistedColumnDecision] = Field(default_factory=list)


class AssistedColumnRoleClassifier:
    """Apply explicit LLM assistance to uncertain or unresolved columns only."""

    def __init__(self, llm_adapter: LLMAdapter) -> None:
        self._llm = llm_adapter

    def classify(
        self,
        *,
        baseline: AnnotatedTable,
        model_name: str | None = None,
    ) -> AnnotatedTable:
        candidate_headers = _candidate_headers(baseline)
        if not candidate_headers:
            return baseline

        prompt = _build_prompt(baseline, candidate_headers)
        raw = self._llm.complete_structured(
            prompt,
            AssistedClassificationResult.model_json_schema(),
            system_prompt=_SYSTEM_PROMPT,
            model_name=model_name,
        )
        try:
            result = AssistedClassificationResult.model_validate(raw)
        except ValidationError as exc:
            raise GroundworkersError(
                "QUERY_ERROR",
                "LLM-assisted source-planning response did not match the expected structure.",
            ) from exc

        return _merge_assisted_decisions(baseline, candidate_headers, result.decisions)

    async def async_classify(
        self,
        *,
        baseline: AnnotatedTable,
        model_name: str | None = None,
    ) -> AnnotatedTable:
        """Classify candidates through the model backend's async API."""

        candidate_headers = _candidate_headers(baseline)
        if not candidate_headers:
            return baseline
        raw = await self._llm.async_complete_structured(
            _build_prompt(baseline, candidate_headers),
            AssistedClassificationResult.model_json_schema(),
            system_prompt=_SYSTEM_PROMPT,
            model_name=model_name,
        )
        try:
            result = AssistedClassificationResult.model_validate(raw)
        except ValidationError as exc:
            raise GroundworkersError(
                "QUERY_ERROR",
                "LLM-assisted source-planning response did not match the expected structure.",
            ) from exc
        return _merge_assisted_decisions(baseline, candidate_headers, result.decisions)


def _candidate_headers(baseline: AnnotatedTable) -> list[str]:
    return [
        header
        for header in baseline.headers
        if header not in baseline.column_annotations or header in baseline.uncertain_columns
    ]


def _build_prompt(
    baseline: AnnotatedTable,
    candidate_headers: list[str],
) -> str:
    lines: list[str] = [
        f"Table name: {baseline.name}",
        f"Headers: {', '.join(baseline.headers)}",
        "",
        "Available roles:",
    ]
    for role, description in COLUMN_ROLE_DESCRIPTIONS.items():
        lines.append(f"- {role.value}: {description}")

    lines.extend(
        [
            "",
            "Current deterministic annotations:",
        ]
    )
    for header in baseline.headers:
        annotation = baseline.column_annotations.get(header)
        if annotation is None:
            lines.append(f"- {header}: unclassified")
            continue
        lines.append(
            f"- {header}: role={annotation.role.value}, confidence={annotation.confidence:.2f}, "
            f"tier={annotation.detection_tier}, inferred_vocab={annotation.inferred_vocab!r}, "
            f"packed_value={annotation.packed_value}"
        )

    lines.extend(
        [
            "",
            "Candidate headers to review:",
            ", ".join(candidate_headers),
            "",
            "Sample rows:",
        ]
    )
    for index, row in enumerate(baseline.sample_rows[:5], start=1):
        parts = [f"{header}={row.get(header, '')!r}" for header in baseline.headers]
        lines.append(f"{index}. " + "; ".join(parts))

    lines.extend(
        [
            "",
            "Return JSON with this shape:",
            '{"decisions": [{"header": "...", "role": "... or null", "confidence": 0.0-1.0, '
            '"inferred_vocab": null, "packed_value": false, "notes": "..."}]}',
            "Only include candidate headers. Use null role when the evidence remains too weak.",
        ]
    )
    return "\n".join(lines)


def _merge_assisted_decisions(
    baseline: AnnotatedTable,
    candidate_headers: list[str],
    decisions: list[AssistedColumnDecision],
) -> AnnotatedTable:
    annotations = dict(baseline.column_annotations)

    for decision in decisions:
        if decision.header not in candidate_headers:
            continue
        if decision.role is None:
            continue
        try:
            role = ColumnRole(decision.role)
        except ValueError:
            continue

        existing = annotations.get(decision.header)
        annotations[decision.header] = ColumnAnnotation(
            role=role,
            detection_tier="LLM",
            confidence=decision.confidence,
            inferred_vocab=decision.inferred_vocab if decision.inferred_vocab is not None else (
                existing.inferred_vocab if existing is not None else None
            ),
            packed_value=decision.packed_value if decision.packed_value is not None else (
                existing.packed_value if existing is not None else False
            ),
            notes=decision.notes or (existing.notes if existing is not None else ""),
        )

    packed_value_columns = [
        header for header, annotation in annotations.items() if annotation.packed_value
    ]
    uncertain_columns = [
        header
        for header, annotation in annotations.items()
        if annotation.confidence < UNCERTAIN_CONFIDENCE_THRESHOLD
    ]
    confidences = [annotation.confidence for annotation in annotations.values()]
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    groundable_count = sum(
        1 for annotation in annotations.values() if annotation.role in _GROUNDABLE_ROLES
    )

    notes = list(baseline.annotation_notes)
    notes.append("llm-assisted classification reviewed uncertain or unresolved columns")

    return replace(
        baseline,
        column_annotations=annotations,
        packed_value_columns=packed_value_columns,
        classification_tier_used="LLM",
        classification_confidence=confidence,
        uncertain_columns=uncertain_columns,
        llm_fallback_used=True,
        fallback_columns=list(candidate_headers),
        groundable_column_count=groundable_count,
        annotation_notes=notes,
    )
