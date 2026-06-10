"""Structural normalization helpers for source-planning artifacts.

The functions in this module stabilize representation only. They may clean
headers, coerce mixed values to text, strip HTML markup, and make duplicate
column names deterministic. They must not assign semantic roles.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html import unescape
import re
from typing import Any, Iterable

from groundworkers.source_planning.models import NormalisedTable, RawTable
from groundworkers.source_planning.provenance import HeaderProvenance
from groundworkers.source_planning.warnings import PlanningWarning

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_PREVIEW_ROWS = 5


@dataclass(kw_only=True, frozen=True)
class NormalisationPolicy:
    """Configuration for structural cleanup.

    The defaults are intentionally conservative:
      - preserve table identity and row order
      - stabilize headers and cell text
      - prune only columns that are structurally empty after cleanup
    """

    prune_empty_columns: bool = True
    empty_header_prefix: str = "column"


def normalise_table(
    table: RawTable,
    *,
    policy: NormalisationPolicy | None = None,
) -> NormalisedTable:
    """Normalize a raw table into a stable structural representation.

    It takes a ``RawTable`` and returns a ``NormalisedTable`` with cleaned
    headers, cleaned cell text, deterministic duplicate handling, conservative
    empty-column pruning, and explicit provenance about the structural changes
    that were made.
    """
    policy = policy or NormalisationPolicy()

    used_headers: Counter[str] = Counter()
    header_provenance: dict[str, HeaderProvenance] = {}
    normalised_headers: list[str] = []
    warnings: list[PlanningWarning] = []
    notes: list[str] = []

    html_seen = False
    duplicate_seen = False
    coercion_seen = False

    for index, original_header in enumerate(table.headers, start=1):
        base_header, operations = _clean_header(original_header)
        if not base_header:
            base_header = f"{policy.empty_header_prefix}_{index}"
            operations.append("empty_header_replaced")
            warnings.append(
                PlanningWarning(
                    code="EMPTY_HEADER_REPLACED",
                    message=f"Empty header at position {index} was replaced deterministically.",
                    table_name=table.name,
                )
            )

        used_headers[base_header] += 1
        if used_headers[base_header] > 1:
            duplicate_seen = True
            final_header = f"{base_header}__{used_headers[base_header]}"
            operations.append("duplicate_suffix")
            warnings.append(
                PlanningWarning(
                    code="DUPLICATE_HEADER_NORMALISED",
                    message=f"Duplicate header {base_header!r} was renamed to {final_header!r}.",
                    table_name=table.name,
                    column_name=final_header,
                )
            )
        else:
            final_header = base_header

        if any(op.startswith("strip_html") for op in operations):
            html_seen = True
        normalised_headers.append(final_header)
        header_provenance[final_header] = HeaderProvenance(
            original=original_header,
            normalised=final_header,
            operations=operations,
        )

    normalised_rows: list[dict[str, str]] = []
    for row in table.rows:
        normalised_row: dict[str, str] = {}
        for original_header, final_header in zip(table.headers, normalised_headers):
            cleaned_value, value_changed, value_had_html, value_was_typed = _clean_cell_text(row.get(original_header, ""))
            normalised_row[final_header] = cleaned_value
            html_seen = html_seen or value_had_html
            coercion_seen = coercion_seen or value_was_typed
            if value_changed and not value_had_html and value_was_typed:
                coercion_seen = True
        normalised_rows.append(normalised_row)

    if policy.prune_empty_columns:
        (
            normalised_headers,
            normalised_rows,
            sample_rows_override,
            empty_columns_pruned,
            prune_warnings,
        ) = _prune_empty_columns(
            headers=normalised_headers,
            rows=normalised_rows,
            table_name=table.name,
        )
        warnings.extend(prune_warnings)
        if empty_columns_pruned:
            notes.append("structurally empty columns were pruned")
            header_provenance = {
                header: provenance
                for header, provenance in header_provenance.items()
                if header in normalised_headers
            }
    else:
        sample_rows_override = None
        empty_columns_pruned = False

    if duplicate_seen:
        notes.append("duplicate headers were normalized deterministically")
    if html_seen:
        notes.append("html-rich text was reduced to cleaned text surfaces")
    if coercion_seen:
        notes.append("mixed cell values were coerced to string surfaces")

    _append_format_specific_notes(table, notes)

    sample_rows = sample_rows_override or normalised_rows[: min(_PREVIEW_ROWS, len(normalised_rows))]

    return NormalisedTable.from_raw(
        table,
        headers=normalised_headers,
        rows=normalised_rows,
        sample_rows=sample_rows,
        header_provenance=header_provenance,
        normalisation_notes=notes,
        warnings=warnings,
    )


def normalise_tables(
    tables: Iterable[RawTable],
    *,
    policy: NormalisationPolicy | None = None,
) -> list[NormalisedTable]:
    """Normalize multiple raw tables with one shared policy."""

    return [normalise_table(table, policy=policy) for table in tables]


def normalise_headers(table: RawTable) -> NormalisedTable:
    """Backward-compatible alias for the PR1 helper name."""

    return normalise_table(table)


def _clean_header(value: Any) -> tuple[str, list[str]]:
    text = "" if value is None else str(value)
    operations: list[str] = []

    if text.startswith("\ufeff"):
        text = text.removeprefix("\ufeff")
        operations.append("strip_bom")

    stripped = text.strip()
    if stripped != text:
        text = stripped
        operations.append("trim_whitespace")

    text, html_changed = _strip_html(text)
    if html_changed:
        operations.append("strip_html_tags")

    collapsed = _collapse_whitespace(unescape(text))
    if collapsed != text:
        text = collapsed
        operations.append("collapse_whitespace")

    return text, operations


def _clean_cell_text(value: Any) -> tuple[str, bool, bool, bool]:
    was_typed = value is not None and not isinstance(value, str)
    text = "" if value is None else str(value)
    original = text

    text, had_html = _strip_html(text)
    text = _collapse_whitespace(unescape(text.strip()))
    changed = text != original
    return text, changed, had_html, was_typed


def _strip_html(text: str) -> tuple[str, bool]:
    stripped = _HTML_TAG_RE.sub(" ", text)
    return stripped, stripped != text


def _collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _prune_empty_columns(
    *,
    headers: list[str],
    rows: list[dict[str, str]],
    table_name: str,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]] | None, bool, list[PlanningWarning]]:
    if not rows:
        return headers, rows, None, False, []

    retained_headers = [
        header
        for header in headers
        if header and any((row.get(header) or "").strip() for row in rows)
    ]

    if len(retained_headers) == len(headers):
        return headers, rows, None, False, []

    dropped_headers = [header for header in headers if header not in retained_headers]
    pruned_rows = [
        {header: row.get(header, "") for header in retained_headers}
        for row in rows
    ]
    warnings = [
        PlanningWarning(
            code="EMPTY_COLUMN_PRUNED",
            message=f"Structurally empty column {header!r} was pruned during normalization.",
            table_name=table_name,
            column_name=header,
        )
        for header in dropped_headers
    ]
    sample_rows = pruned_rows[: min(_PREVIEW_ROWS, len(pruned_rows))]
    return retained_headers, pruned_rows, sample_rows, True, warnings


def _append_format_specific_notes(table: RawTable, notes: list[str]) -> None:
    if table.metadata.get("banner_row_removed"):
        notes.append("a banner/title row was removed during format-specific extraction")
