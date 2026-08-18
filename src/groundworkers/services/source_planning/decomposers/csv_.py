"""CSV decomposer — produces a single ``RawTable`` from CSV bytes."""

from __future__ import annotations

import csv
import io

from groundworkers.services.source_planning.models import RawTable, SourceFormat

_MAX_SAMPLE = 5


def decompose(content: bytes, filename: str | None = None) -> list[RawTable]:
    """Decode CSV bytes and return one ``RawTable``."""

    text, encoding_used = _decode(content)
    dialect = _sniff_dialect(text)

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers: list[str] = [header for header in (reader.fieldnames or []) if header is not None]

    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({key: (value or "") for key, value in row.items() if key is not None})

    return [
        RawTable(
            name=_table_name(filename),
            headers=headers,
            rows=rows,
            sample_rows=rows[:_MAX_SAMPLE],
            source_format=SourceFormat.CSV,
            row_count=len(rows),
            metadata={"encoding_used": encoding_used},
        )
    ]


def _decode(content: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("latin-1", errors="replace"), "latin-1"


def _sniff_dialect(text: str) -> type[csv.Dialect]:
    """Dialect *class*, which is what ``Sniffer.sniff`` returns and what the
    ``csv`` readers accept alongside an instance."""
    try:
        return csv.Sniffer().sniff(text[:8_192], delimiters=",\t|;")
    except csv.Error:
        return csv.excel


def _table_name(filename: str | None) -> str:
    if not filename:
        return "table"
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base or "table"
