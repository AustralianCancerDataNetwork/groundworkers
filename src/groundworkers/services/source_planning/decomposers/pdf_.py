"""PDF decomposer for grid tables with page-text fallback."""

from __future__ import annotations

import io
import re

from groundworkers.services.source_planning.models import RawTable, SourceFormat

_MAX_SAMPLE = 5
_MAX_TABLES = 10
_MULTI_SPACE = re.compile(r"\s+")


def decompose(content: bytes, filename: str | None = None) -> list[RawTable]:
    """Parse PDF bytes and return one ``RawTable`` per distinct table shape."""

    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(content))
    except Exception:
        return []

    with pdf:
        table_rows: dict[tuple[str, ...], list[dict[str, str]]] = {}
        table_order: list[tuple[str, ...]] = []
        text_rows: list[dict[str, str]] = []

        for page_num, page in enumerate(pdf.pages, start=1):
            page_tables = page.extract_tables() or []
            page_had_table = False
            for raw_table in page_tables:
                if not raw_table or len(raw_table) < 2:
                    continue
                headers = _normalise_headers(raw_table[0])
                if not headers or all(header == "" for header in headers):
                    continue
                key = tuple(headers)
                if key not in table_rows:
                    table_rows[key] = []
                    table_order.append(key)
                for raw_row in raw_table[1:]:
                    row = {headers[i]: _cell(raw_row[i] if i < len(raw_row) else None) for i in range(len(headers))}
                    if any(value for value in row.values()):
                        table_rows[key].append(row)
                page_had_table = True

            if not page_had_table:
                text = (page.extract_text() or "").strip()
                if text:
                    text_rows.append({"page_number": str(page_num), "text": text})

    results: list[RawTable] = []
    for key in table_order[:_MAX_TABLES]:
        rows = table_rows[key]
        if not rows:
            continue
        headers = list(key)
        results.append(
            RawTable(
                name=_table_name(filename, len(results)),
                headers=headers,
                rows=rows,
                sample_rows=rows[:_MAX_SAMPLE],
                source_format=SourceFormat.PDF,
                row_count=len(rows),
                metadata={"pdf_source": "table"},
            )
        )

    if not results and text_rows:
        results.append(
            RawTable(
                name=_table_name(filename, 0),
                headers=["page_number", "text"],
                rows=text_rows,
                sample_rows=text_rows[:_MAX_SAMPLE],
                source_format=SourceFormat.PDF,
                row_count=len(text_rows),
                metadata={"pdf_source": "text"},
            )
        )
    return results


def _cell(value: object) -> str:
    if value is None:
        return ""
    return _MULTI_SPACE.sub(" ", str(value)).strip()


def _normalise_headers(raw: list) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for cell in raw:
        header = _cell(cell) or "col"
        if header in seen:
            seen[header] += 1
            header = f"{header}_{seen[header]}"
        else:
            seen[header] = 1
        out.append(header)
    return out


def _table_name(filename: str | None, index: int) -> str:
    base = ""
    if filename:
        base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." in base:
            base = base.rsplit(".", 1)[0]
    return f"{base}_table{index + 1}" if base else f"table{index + 1}"
