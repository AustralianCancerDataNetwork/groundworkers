"""Composition facade for stateless source planning."""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.assisted import AssistedColumnRoleClassifier
from groundworkers.services.source_planning.classifier import ColumnRoleClassifier
from groundworkers.services.source_planning.decomposer import TableDecomposer
from groundworkers.services.source_planning.detector import FormatDetector
from groundworkers.services.source_planning.models import (
    AnnotatedTable,
    IngestionPlan,
    IngestionStrategy,
    NormalisedTable,
    PreIngestBundle,
    RawTable,
    SourceFormat,
    UNCERTAIN_CONFIDENCE_THRESHOLD,
)
from groundworkers.services.source_planning.normalisation import (
    NormalisationPolicy,
    normalise_tables,
)
from groundworkers.services.source_planning.router import IngesterRouter
from groundworkers.services.source_planning.warnings import PlanningError, PlanningWarning

class SourcePlanningService:
    """Thin composition root for the stateless source-planning pipeline.

    This service owns stage sequencing only. It detects source format,
    decomposes content into tables, normalises structure, classifies columns,
    routes tables to ingestion strategies, and packages the result as a
    ``PreIngestBundle``.
    """

    def __init__(
        self,
        *,
        detector: FormatDetector | None = None,
        decomposer: TableDecomposer | None = None,
        classifier: ColumnRoleClassifier | None = None,
        assisted_classifier: AssistedColumnRoleClassifier | None = None,
        router: IngesterRouter | None = None,
        normalisation_policy: NormalisationPolicy | None = None,
    ) -> None:
        self._detector = detector or FormatDetector()
        self._decomposer = decomposer or TableDecomposer()
        self._classifier = classifier or ColumnRoleClassifier()
        self._assisted_classifier = assisted_classifier
        self._router = router or IngesterRouter()
        self._normalisation_policy = normalisation_policy

    def plan_source(
        self,
        content: str | bytes,
        *,
        filename: str | None = None,
        caller_hint: str | None = None,
    ) -> PreIngestBundle:
        """Plan one submitted source artifact end to end."""

        started = perf_counter()
        raw_content = _coerce_content_bytes(content)
        source_format = self._detector.detect(raw_content, filename)
        raw_tables = self._decomposer.decompose(raw_content, source_format, filename)
        bundle = self._plan_from_raw_tables(
            raw_tables,
            source_format=source_format,
            caller_hint=caller_hint,
            elapsed_ms=_elapsed_ms(started),
        )
        return bundle

    def plan_tables(
        self,
        tables: Sequence[RawTable],
        *,
        caller_hint: str | None = None,
    ) -> PreIngestBundle:
        """Plan already-decomposed tables."""

        started = perf_counter()
        raw_tables = list(tables)
        source_format = raw_tables[0].source_format if raw_tables else SourceFormat.CSV
        return self._plan_from_raw_tables(
            raw_tables,
            source_format=source_format,
            caller_hint=caller_hint,
            elapsed_ms=_elapsed_ms(started),
        )

    def plan_source_assisted(
        self,
        content: str | bytes,
        *,
        filename: str | None = None,
        caller_hint: str | None = None,
    ) -> PreIngestBundle:
        """Plan one submitted source artifact with explicit LLM assistance."""

        started = perf_counter()
        raw_content = _coerce_content_bytes(content)
        source_format = self._detector.detect(raw_content, filename)
        raw_tables = self._decomposer.decompose(raw_content, source_format, filename)
        return self._plan_from_raw_tables(
            raw_tables,
            source_format=source_format,
            caller_hint=caller_hint,
            elapsed_ms=_elapsed_ms(started),
            use_assisted_classification=True,
        )

    def plan_tables_assisted(
        self,
        tables: Sequence[RawTable],
        *,
        caller_hint: str | None = None,
    ) -> PreIngestBundle:
        """Plan already-decomposed tables with explicit LLM assistance."""

        started = perf_counter()
        raw_tables = list(tables)
        source_format = raw_tables[0].source_format if raw_tables else SourceFormat.CSV
        return self._plan_from_raw_tables(
            raw_tables,
            source_format=source_format,
            caller_hint=caller_hint,
            elapsed_ms=_elapsed_ms(started),
            use_assisted_classification=True,
        )

    def classify_columns(self, table: NormalisedTable) -> AnnotatedTable:
        """Expose deterministic classification directly when needed."""

        return self._classifier.classify(table)

    def _plan_from_raw_tables(
        self,
        raw_tables: list[RawTable],
        *,
        source_format: SourceFormat,
        caller_hint: str | None,
        elapsed_ms: int,
        use_assisted_classification: bool = False,
    ) -> PreIngestBundle:
        warnings: list[PlanningWarning] = []
        errors: list[PlanningError] = []

        if not raw_tables:
            errors.append(
                PlanningError(
                    code="NO_TABLES_EXTRACTED",
                    message="No tables were produced by stateless source planning.",
                )
            )
            plan = IngestionPlan(
                format_detected=source_format,
                caller_hint=caller_hint,
                hint_matches=_hint_matches(caller_hint, source_format, []),
                warnings=warnings,
                errors=errors,
            )
            return PreIngestBundle(plan=plan, raw_tables=[], warnings=warnings, errors=errors, elapsed_ms=elapsed_ms)

        warnings.extend(_mixed_format_warnings(raw_tables, source_format))

        normalised_tables = normalise_tables(raw_tables, policy=self._normalisation_policy)
        annotated_tables = [self._classifier.classify(table) for table in normalised_tables]
        if use_assisted_classification:
            if self._assisted_classifier is None:
                raise GroundworkersError(
                    "BACKEND_UNAVAIL",
                    "LLM-assisted source planning is unavailable because no LLM adapter is configured.",
                )
            annotated_tables = [
                self._assisted_classifier.classify(baseline=annotated)
                if _table_needs_assistance(annotated) else annotated
                for annotated in annotated_tables
            ]

        strategies: list[IngestionStrategy] = []
        routed_tables: list[AnnotatedTable] = []
        for table in annotated_tables:
            strategy, routed = self._router.route(table)
            strategies.append(strategy)
            routed_tables.append(routed)

        for table in routed_tables:
            warnings.extend(table.warnings)

        uncertain_tables = _collect_uncertain_tables(routed_tables, strategies)
        hint_matches = _hint_matches(caller_hint, source_format, strategies)
        if caller_hint and not hint_matches:
            warnings.append(
                PlanningWarning(
                    code="HINT_MISMATCH",
                    message=(
                        f"Caller hint {caller_hint!r} did not align with detected format "
                        f"{source_format.value!r} or routed strategies."
                    ),
                )
            )

        plan = IngestionPlan(
            format_detected=source_format,
            caller_hint=caller_hint,
            hint_matches=hint_matches,
            tables=routed_tables,
            strategies=strategies,
            warnings=warnings,
            errors=errors,
            uncertain_tables=uncertain_tables,
        )
        llm_tier_used = any(table.llm_fallback_used for table in routed_tables)

        return PreIngestBundle(
            plan=plan,
            raw_tables=raw_tables,
            normalised_tables=normalised_tables,
            annotated_tables=routed_tables,
            warnings=warnings,
            errors=errors,
            elapsed_ms=elapsed_ms,
            llm_tier_used=llm_tier_used,
        )


def plan_source(
    content: str | bytes,
    *,
    filename: str | None = None,
    caller_hint: str | None = None,
) -> PreIngestBundle:
    """Convenience wrapper for one-shot source planning."""

    return SourcePlanningService().plan_source(content, filename=filename, caller_hint=caller_hint)


def plan_tables(
    tables: Sequence[RawTable],
    *,
    caller_hint: str | None = None,
) -> PreIngestBundle:
    """Convenience wrapper for planning already-decomposed tables."""

    return SourcePlanningService().plan_tables(tables, caller_hint=caller_hint)


def _coerce_content_bytes(content: str | bytes) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _mixed_format_warnings(raw_tables: list[RawTable], source_format: SourceFormat) -> list[PlanningWarning]:
    formats = {table.source_format for table in raw_tables}
    if len(formats) <= 1:
        return []
    found = ", ".join(sorted(format_.value for format_ in formats))
    return [
        PlanningWarning(
            code="MIXED_SOURCE_FORMATS",
            message=(
                f"Planning received tables with mixed source formats ({found}); "
                f"using {source_format.value!r} as the primary plan format."
            ),
        )
    ]


def _collect_uncertain_tables(
    tables: list[AnnotatedTable],
    strategies: list[IngestionStrategy],
) -> list[dict[str, object]]:
    uncertain: list[dict[str, object]] = []
    for table, strategy in zip(tables, strategies):
        reason_parts: list[str] = []
        if table.uncertain_columns:
            reason_parts.append("uncertain column roles remain")
        if table.classification_confidence is not None and table.classification_confidence < UNCERTAIN_CONFIDENCE_THRESHOLD:
            reason_parts.append("overall classification confidence was below threshold")
        warning_codes = {warning.code for warning in table.warnings}
        if "AMBIGUOUS_DOMAIN_HINT" in warning_codes:
            reason_parts.append("domain hint remained ambiguous")
        if strategy == IngestionStrategy.UNSUPPORTED and table.non_target_reason:
            reason_parts.append(table.non_target_reason)

        if not reason_parts:
            continue

        uncertain.append(
            {
                "table_name": table.name,
                "groundable_column_count": table.groundable_column_count,
                "classification_tier_used": table.classification_tier_used,
                "proposed_strategy": strategy.value,
                "uncertainty_reason": "; ".join(reason_parts),
            }
        )
    return uncertain


def _table_needs_assistance(table: AnnotatedTable) -> bool:
    if table.classification_tier_used == "quick_reject":
        return False
    if table.uncertain_columns:
        return True
    if table.classification_confidence is not None and table.classification_confidence < UNCERTAIN_CONFIDENCE_THRESHOLD:
        return True
    if table.groundable_column_count == 0:
        return True
    return False


def _hint_matches(
    caller_hint: str | None,
    source_format: SourceFormat,
    strategies: Sequence[IngestionStrategy],
) -> bool:
    if not caller_hint:
        return True

    key = _normalize_hint(caller_hint)
    format_hint = _HINT_TO_FORMAT.get(key)
    if format_hint is not None:
        return format_hint == source_format

    strategy_matcher = _HINT_TO_STRATEGY_MATCHER.get(key)
    if strategy_matcher is not None:
        return any(strategy_matcher(strategy) for strategy in strategies)

    return True


def _normalize_hint(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


_HINT_TO_FORMAT: dict[str, SourceFormat] = {
    "csv": SourceFormat.CSV,
    "xlsx": SourceFormat.XLSX,
    "xml": SourceFormat.XML,
    "json": SourceFormat.JSON,
    "ddl": SourceFormat.DDL_SQL,
    "ddlsql": SourceFormat.DDL_SQL,
    "pdf": SourceFormat.PDF,
    "docx": SourceFormat.DOCX,
}

_HINT_TO_STRATEGY_MATCHER = {
    "datadict": lambda strategy: strategy
    in {
        IngestionStrategy.DATA_DICT_IDEAL,
        IngestionStrategy.DATA_DICT_SCHEMA,
        IngestionStrategy.DATA_DICT_PACKED_VALUES,
    },
    "datadictideal": lambda strategy: strategy == IngestionStrategy.DATA_DICT_IDEAL,
    "datadictschema": lambda strategy: strategy == IngestionStrategy.DATA_DICT_SCHEMA,
    "datadictpackedvalues": lambda strategy: strategy == IngestionStrategy.DATA_DICT_PACKED_VALUES,
    "redcap": lambda strategy: strategy == IngestionStrategy.DATA_DICT_PACKED_VALUES,
    "unsupported": lambda strategy: strategy == IngestionStrategy.UNSUPPORTED,
}
