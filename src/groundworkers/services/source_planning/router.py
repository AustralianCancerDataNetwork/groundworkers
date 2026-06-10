"""Deterministic routing from annotated tables to ingestion strategies."""

from __future__ import annotations

from dataclasses import replace

from groundworkers.services.source_planning.models import (
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
    IngestionStrategy,
)
from groundworkers.services.source_planning.warnings import PlanningWarning

_DOMAIN_HINT_MIN_CONFIDENCE = 0.8

# Intentionally conservative OMOP vocabulary-to-domain hints.
# SNOMED is omitted because it spans many domains.
_VOCAB_TO_DOMAIN: dict[str, str] = {
    "ICD10CM": "Condition",
    "ICD9CM": "Condition",
    "LOINC": "Measurement",
    "NDC": "Drug",
    "RxNorm": "Drug",
    "ATC": "Drug",
    "CPT4": "Procedure",
    "HCPCS": "Procedure",
    "DRG": "Procedure",
}


class IngesterRouter:
    """Map semantically annotated tables to ingestion strategies.

    The router stays deterministic and stateless. It does not create chunks,
    items, or review records; it only turns semantic table annotation into a
    planning decision that caller-facing orchestration can consume.
    """

    def route(self, table: AnnotatedTable) -> tuple[IngestionStrategy, AnnotatedTable]:
        """Return ``(strategy, routed_table)`` for one annotated table."""

        strategy = self._assign_strategy(table)
        routed = self._apply_strategy_annotations(table, strategy)
        routed = self._assign_domain_hint(routed)
        routed = self._append_uncertainty_warning(routed)
        return strategy, routed

    def route_tables(self, tables: list[AnnotatedTable]) -> list[tuple[IngestionStrategy, AnnotatedTable]]:
        """Route multiple annotated tables."""

        return [self.route(table) for table in tables]

    def _assign_strategy(self, table: AnnotatedTable) -> IngestionStrategy:
        if not table.is_grounding_target:
            return IngestionStrategy.UNSUPPORTED

        annotations = table.column_annotations
        has_codes = _any_role(annotations, ColumnRole.codes)
        has_label = _any_role(annotations, ColumnRole.label)
        has_desc = _any_role(annotations, ColumnRole.description)
        has_values = _any_role(annotations, ColumnRole.values)
        has_vocab = _any_role(annotations, ColumnRole.source_vocab)
        has_attribute = _any_role(annotations, ColumnRole.attribute)
        has_packed_values = bool(table.packed_value_columns)

        if has_codes and (has_label or has_desc or has_attribute):
            return IngestionStrategy.DATA_DICT_IDEAL

        if (has_label or has_attribute) and (has_packed_values or has_values) and not has_codes:
            return IngestionStrategy.DATA_DICT_PACKED_VALUES

        if has_label or has_vocab or has_attribute:
            return IngestionStrategy.DATA_DICT_SCHEMA

        return IngestionStrategy.UNSUPPORTED

    def _apply_strategy_annotations(self, table: AnnotatedTable, strategy: IngestionStrategy) -> AnnotatedTable:
        warnings = list(table.warnings)
        notes = list(table.annotation_notes)

        if strategy == IngestionStrategy.UNSUPPORTED:
            is_non_target = not table.is_grounding_target
            if is_non_target:
                warnings.append(
                    PlanningWarning(
                        code="UNSUPPORTED_NON_TARGET",
                        message=table.non_target_reason or "Table is already marked as a non-target.",
                        table_name=table.name,
                    )
                )
                return replace(table, warnings=warnings)

            warnings.append(
                PlanningWarning(
                    code="UNSUPPORTED_NO_ROUTE",
                    message="Annotated table did not match any supported ingestion strategy.",
                    table_name=table.name,
                )
            )
            return replace(
                table,
                is_grounding_target=False,
                non_target_reason="No supported ingestion strategy matched the annotated table.",
                warnings=warnings,
            )

        if strategy == IngestionStrategy.DATA_DICT_PACKED_VALUES:
            warnings.append(
                PlanningWarning(
                    code="PACKED_VALUES_ROUTE",
                    message="Packed-value columns were detected; downstream ingestion should expand value sets explicitly.",
                    table_name=table.name,
                )
            )
            notes.append("router selected packed-values strategy based on label/attribute structure and value-set columns")
            return replace(table, warnings=warnings, annotation_notes=notes)

        return table

    def _assign_domain_hint(self, table: AnnotatedTable) -> AnnotatedTable:
        candidates: list[tuple[str, float]] = []
        for annotation in table.column_annotations.values():
            if annotation.role != ColumnRole.codes:
                continue
            if not annotation.inferred_vocab or annotation.confidence < _DOMAIN_HINT_MIN_CONFIDENCE:
                continue
            domain = _VOCAB_TO_DOMAIN.get(annotation.inferred_vocab)
            if domain is not None:
                candidates.append((domain, annotation.confidence))

        if not candidates:
            return table

        unique_domains = {domain for domain, _ in candidates}
        if len(unique_domains) > 1:
            warnings = list(table.warnings)
            warnings.append(
                PlanningWarning(
                    code="AMBIGUOUS_DOMAIN_HINT",
                    message="Multiple code columns implied different OMOP domain hints; no table-level domain was assigned.",
                    table_name=table.name,
                )
            )
            return replace(table, warnings=warnings, domain_hint=None, domain_hint_confidence=None)

        domain = candidates[0][0]
        confidence = max(confidence for _, confidence in candidates)
        return replace(table, domain_hint=domain, domain_hint_confidence=confidence)

    def _append_uncertainty_warning(self, table: AnnotatedTable) -> AnnotatedTable:
        if not table.uncertain_columns and (table.classification_confidence or 1.0) >= 0.8:
            return table

        warnings = list(table.warnings)
        if table.uncertain_columns:
            warnings.append(
                PlanningWarning(
                    code="UNCERTAIN_COLUMNS",
                    message=(
                        "Deterministic classification left some columns uncertain: "
                        + ", ".join(table.uncertain_columns)
                    ),
                    table_name=table.name,
                )
            )
        elif (table.classification_confidence or 0.0) < 0.8:
            warnings.append(
                PlanningWarning(
                    code="LOW_CLASSIFICATION_CONFIDENCE",
                    message="Overall deterministic classification confidence was below the strong-acceptance threshold.",
                    table_name=table.name,
                )
            )
        return replace(table, warnings=warnings)


def route_table(table: AnnotatedTable) -> tuple[IngestionStrategy, AnnotatedTable]:
    """Convenience wrapper for routing one annotated table."""

    return IngesterRouter().route(table)


def route_tables(tables: list[AnnotatedTable]) -> list[tuple[IngestionStrategy, AnnotatedTable]]:
    """Convenience wrapper for routing many annotated tables."""

    return IngesterRouter().route_tables(tables)


def _any_role(annotations: dict[str, ColumnAnnotation], role: ColumnRole) -> bool:
    return any(annotation.role == role for annotation in annotations.values())
