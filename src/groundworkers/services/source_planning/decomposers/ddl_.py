"""Generic SQL DDL decomposer."""

from __future__ import annotations

import re

from groundworkers.services.source_planning.models import RawTable, SourceFormat

_HEADERS = ["column_name", "data_type", "is_pk", "fk_table"]
_MAX_SAMPLE = 5
_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+|LOCAL\s+)?"
    r"(?:TEMPORARY\s+|TEMP\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([\w\"\'\`\[\].]+)"
    r"\s*\(",
    re.IGNORECASE,
)
_TABLE_CONSTRAINT_RE = re.compile(
    r"^\s*(?:CONSTRAINT\s+\S+\s+)?"
    r"(?:PRIMARY\s+KEY|UNIQUE(?:\s+KEY)?|FOREIGN\s+KEY|"
    r"INDEX|KEY|CHECK|FULLTEXT|SPATIAL)\b",
    re.IGNORECASE,
)
_CONSTRAINT_KW_RE = re.compile(
    r"\b(?:NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|UNIQUE|REFERENCES|"
    r"CONSTRAINT|CHECK|GENERATED|AUTO_?INCREMENT|SERIAL|"
    r"ON\s+(?:DELETE|UPDATE)|COLLATE|CHARACTER\s+SET|COMMENT)\b",
    re.IGNORECASE,
)
_IDENT_RE = re.compile(
    r'^(?:"([^"]*)"'
    r"|`([^`]*)`"
    r"|\[([^\]]*)\]"
    r"|'([^']*)')"
    r"|([A-Za-z_][A-Za-z0-9_$]*)",
    re.IGNORECASE,
)
_REFERENCES_RE = re.compile(r"\bREFERENCES\s+([\w\"\`\[\].]+)(?:\s*\(([^)]+)\))?", re.IGNORECASE)
_TABLE_PK_RE = re.compile(r"\bPRIMARY\s+KEY\s*\(([^)]+)\)", re.IGNORECASE)
_TABLE_FK_RE = re.compile(
    r"\bFOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+([\w\"\`\[\].]+)(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def decompose(content: bytes, filename: str | None = None) -> list[RawTable]:
    """Parse DDL bytes into one ``RawTable`` per ``CREATE TABLE`` block."""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError:
            return []

    text = _strip_comments(text)
    tables: list[RawTable] = []
    pos = 0
    while pos < len(text):
        match = _CREATE_TABLE_RE.search(text, pos)
        if not match:
            break
        table_name = _extract_local_name(match.group(1))
        body, body_end = _extract_paren_body(text, match.end() - 1)
        pos = body_end
        if body is None:
            continue
        if re.match(r"^SELECT\b", body.strip(), re.IGNORECASE):
            continue

        rows, pk_cols, fk_map = _parse_column_list(body)
        if not rows:
            continue
        for row in rows:
            col_name = row["column_name"]
            if col_name in pk_cols:
                row["is_pk"] = "YES"
            if col_name in fk_map and not row["fk_table"]:
                row["fk_table"] = fk_map[col_name]

        tables.append(
            RawTable(
                name=table_name,
                headers=_HEADERS,
                rows=rows,
                sample_rows=rows[:_MAX_SAMPLE],
                source_format=SourceFormat.DDL_SQL,
                row_count=len(rows),
                metadata={"sql_table_name": table_name},
            )
        )

    return tables


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub(" ", text))


def _extract_paren_body(text: str, open_pos: int) -> tuple[str | None, int]:
    if open_pos >= len(text) or text[open_pos] != "(":
        return None, open_pos + 1
    depth = 0
    in_string: str | None = None
    for index in range(open_pos, len(text)):
        char = text[index]
        if in_string:
            if char == in_string:
                in_string = None
        elif char in ("'", '"', "`"):
            in_string = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : index], index + 1
    return None, len(text)


def _extract_local_name(raw: str) -> str:
    return raw.rsplit(".", 1)[-1].strip('"\'`[] ')


def _parse_column_list(body: str) -> tuple[list[dict[str, str]], set[str], dict[str, str]]:
    parts = _split_at_depth_zero(body)
    rows: list[dict[str, str]] = []
    pk_cols: set[str] = set()
    fk_map: dict[str, str] = {}

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if _TABLE_CONSTRAINT_RE.match(part):
            pk_match = _TABLE_PK_RE.search(part)
            if pk_match:
                for col in pk_match.group(1).split(","):
                    pk_cols.add(_extract_local_name(col.strip()))
            fk_match = _TABLE_FK_RE.search(part)
            if fk_match:
                src_cols = [_extract_local_name(col.strip()) for col in fk_match.group(1).split(",")]
                ref_table = _extract_local_name(fk_match.group(2))
                ref_cols = [_extract_local_name(col.strip()) for col in fk_match.group(3).split(",")] if fk_match.group(3) else []
                for index, src in enumerate(src_cols):
                    ref_col = ref_cols[index] if index < len(ref_cols) else ""
                    fk_map[src] = f"{ref_table}.{ref_col}" if ref_col else ref_table
            continue

        row = _parse_column_def(part)
        if row:
            rows.append(row)

    return rows, pk_cols, fk_map


def _parse_column_def(line: str) -> dict[str, str] | None:
    ident_match = _IDENT_RE.match(line.strip())
    if not ident_match:
        return None
    col_name = next(group for group in ident_match.groups() if group is not None)
    rest = line.strip()[ident_match.end() :].strip()
    kw_match = _CONSTRAINT_KW_RE.search(rest)
    if kw_match:
        data_type = rest[: kw_match.start()].strip()
        modifiers = rest[kw_match.start() :]
    else:
        data_type = rest
        modifiers = ""
    data_type = re.sub(r"\s{2,}", " ", data_type).strip().rstrip(",")
    if not data_type:
        return None
    is_pk = "YES" if re.search(r"\bPRIMARY\s+KEY\b", modifiers, re.IGNORECASE) else ""
    fk_table = ""
    ref_match = _REFERENCES_RE.search(modifiers)
    if ref_match:
        ref_table = _extract_local_name(ref_match.group(1))
        ref_col = _extract_local_name(ref_match.group(2)) if ref_match.group(2) else ""
        fk_table = f"{ref_table}.{ref_col}" if ref_col else ref_table
    return {
        "column_name": col_name,
        "data_type": data_type,
        "is_pk": is_pk,
        "fk_table": fk_table,
    }


def _split_at_depth_zero(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_string: str | None = None
    current: list[str] = []
    for char in body:
        if in_string:
            current.append(char)
            if char == in_string:
                in_string = None
        elif char in ("'", '"', "`"):
            in_string = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts
