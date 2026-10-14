from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from groundworkers.application.setup.databases import classify_connection_error
from groundworkers.application.setup.models import (
    CoverageScope,
    CoverageSnapshot,
    VocabularyCoverage,
)

FILTERED_AGGREGATE_BLOCKER = (
    "The installed omop-emb aggregate cannot apply standard, validity, or domain "
    "filters. Filtered population planning is disabled until the backend exposes "
    "a pushed-down aggregate for the same scope."
)


def load_coverage(
    scope: CoverageScope,
    *,
    backend: Any,
    eligible_counter: Callable[[CoverageScope], Mapping[str, int]],
) -> CoverageSnapshot:
    """Load filter-consistent counts without materialising concept identifiers."""

    if scope.standard_only or scope.valid_only or scope.domains:
        return CoverageSnapshot(
            scope=scope,
            available=False,
            blocker=FILTERED_AGGREGATE_BLOCKER,
            metadata={"omop_emb_capability": "unfiltered_by_vocabulary_only"},
        )
    try:
        eligible = _coerce_counts(eligible_counter(scope))
        embedded = _coerce_counts(
            backend.get_embedding_count_by_vocabulary(model_name=scope.model_name)
        )
    except Exception as exc:
        return CoverageSnapshot(
            scope=scope,
            available=False,
            blocker=classify_connection_error(exc).detail,
        )
    return calculate_coverage(scope, eligible=eligible, embedded=embedded)


def calculate_coverage(
    scope: CoverageScope,
    *,
    eligible: Mapping[str, int],
    embedded: Mapping[str, int],
) -> CoverageSnapshot:
    """Calculate per-vocabulary coverage for counts sharing one exact scope."""

    rows: list[VocabularyCoverage] = []
    for vocabulary in scope.vocabularies:
        eligible_count = int(eligible.get(vocabulary, 0))
        embedded_count = int(embedded.get(vocabulary, 0))
        if eligible_count < 0 or embedded_count < 0:
            return _invalid_counts(scope, "Coverage counts cannot be negative.")
        if embedded_count > eligible_count:
            return _invalid_counts(
                scope,
                f"Stored count exceeds eligible count for {vocabulary} under this scope.",
            )
        pending = eligible_count - embedded_count
        percentage = (
            round((embedded_count / eligible_count) * 100, 1) if eligible_count else 0.0
        )
        rows.append(
            VocabularyCoverage(
                vocabulary=vocabulary,
                eligible=eligible_count,
                embedded=embedded_count,
                pending=pending,
                coverage_percent=percentage,
            )
        )

    eligible_total = sum(row.eligible for row in rows)
    embedded_total = sum(row.embedded for row in rows)
    return CoverageSnapshot(
        scope=scope,
        available=True,
        rows=tuple(rows),
        eligible_total=eligible_total,
        embedded_total=embedded_total,
        pending_total=eligible_total - embedded_total,
        metadata={"filter_consistent": True, "aggregate": "pushed_down"},
    )


def _invalid_counts(scope: CoverageScope, blocker: str) -> CoverageSnapshot:
    return CoverageSnapshot(scope=scope, available=False, blocker=blocker)


def _coerce_counts(counts: Any) -> dict[str, int]:
    return {
        str(vocabulary): int(count)
        for vocabulary, count in dict(counts).items()
    }
