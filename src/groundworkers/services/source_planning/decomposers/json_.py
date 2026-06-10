"""Generic JSON decomposer for array-of-object record structures."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from groundworkers.services.source_planning.models import RawTable, SourceFormat

_MAX_SAMPLE = 5
_MAX_TABLES = 5
_MAX_DEPTH = 8
_MIN_RECORD_COUNT = 2
_MIN_COLUMN_COUNT = 2
_MAX_HEADERS = 120
_HEADER_MIN_FRACTION = 0.05
_VALUE_JOINER = " | "


def decompose(content: bytes, filename: str | None = None) -> list[RawTable]:
    """Parse JSON bytes and return one ``RawTable`` per candidate record array."""

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []

    raw: dict[str, list[dict[str, Any]]] = {}
    _collect_arrays(data, "", raw, depth=0)
    if not raw:
        return []

    scores: dict[str, float] = {}
    for path, records in raw.items():
        sample = records[:_MAX_SAMPLE]
        flat_sizes = [len(_flatten_record_all(record)) for record in sample]
        avg = sum(flat_sizes) / len(flat_sizes) if flat_sizes else 0.0
        if avg < _MIN_COLUMN_COUNT:
            continue
        density = _calc_density(sample)
        scores[path] = math.log2(len(records)) * avg * (0.5 + density)

    if not scores:
        return []

    top_paths = sorted(scores, key=scores.__getitem__, reverse=True)[:_MAX_TABLES]
    use_path_names = len(top_paths) > 1
    tables: list[RawTable] = []
    for path in top_paths:
        records = raw[path]
        headers = _collect_headers(records)
        if not headers:
            continue
        rows = [_record_to_row(record, headers) for record in records]
        name = path if use_path_names else _table_name(filename, path)
        tables.append(
            RawTable(
                name=name,
                headers=headers,
                rows=rows,
                sample_rows=rows[:_MAX_SAMPLE],
                source_format=SourceFormat.JSON,
                row_count=len(records),
                metadata={"json_array_path": path},
            )
        )

    return tables


def _collect_arrays(value: Any, path: str, found: dict[str, list[dict[str, Any]]], depth: int) -> None:
    if depth > _MAX_DEPTH:
        return

    if isinstance(value, list):
        dicts = [item for item in value if isinstance(item, dict)]
        if len(dicts) >= _MIN_RECORD_COUNT and len(dicts) * 2 >= len(value):
            key = path or "root"
            found[key] = found.get(key, []) + dicts
            for item in dicts:
                for key_name, child in item.items():
                    if isinstance(child, (list, dict)):
                        _collect_arrays(child, key_name, found, depth + 1)
    elif isinstance(value, dict):
        for key_name, child in value.items():
            child_path = f"{path}.{key_name}" if path else key_name
            _collect_arrays(child, child_path, found, depth + 1)


def _flatten_record_all(
    obj: Any,
    prefix: str = "",
    result: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    if result is None:
        result = {}

    if isinstance(obj, dict):
        for key_name, child in obj.items():
            nested = f"{prefix}.{key_name}" if prefix else key_name
            _flatten_record_all(child, nested, result)
    elif isinstance(obj, list):
        scalars = [str(item) for item in obj if item is not None and not isinstance(item, (dict, list))]
        if scalars and prefix:
            result.setdefault(prefix, []).extend(scalars)
    elif obj is not None and prefix:
        result.setdefault(prefix, []).append(str(obj))

    return result


def _record_to_row(record: dict[str, Any], headers: list[str]) -> dict[str, str]:
    flattened = _flatten_record_all(record)
    return {header: _VALUE_JOINER.join(value for value in flattened.get(header, []) if value) for header in headers}


def _collect_headers(records: list[dict[str, Any]]) -> list[str]:
    first_seen: dict[str, int] = {}
    counts: Counter[str] = Counter()

    for record in records:
        for path, values in _flatten_record_all(record).items():
            if not any(values):
                continue
            if path not in first_seen:
                first_seen[path] = len(first_seen)
            counts[path] += 1

    if not counts:
        return []

    min_count = _minimum_header_count(len(records))
    selected = [path for path in counts if counts[path] >= min_count]
    if len(selected) > _MAX_HEADERS:
        selected = sorted(selected, key=lambda path: (-counts[path], first_seen[path]))[:_MAX_HEADERS]
    return sorted(selected, key=first_seen.__getitem__)


def _calc_density(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    all_headers: set[str] = set()
    flattened = [_flatten_record_all(record) for record in records]
    for row in flattened:
        all_headers.update(row.keys())
    if not all_headers:
        return 0.0
    non_empty = sum(
        1
        for row in flattened
        for header in all_headers
        if any(value for value in row.get(header, []))
    )
    return non_empty / (len(flattened) * len(all_headers))


def _table_name(filename: str | None, path: str) -> str:
    if filename:
        base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." in base:
            base = base.rsplit(".", 1)[0]
        if base:
            return base
    return path or "root"


def _minimum_header_count(record_count: int) -> int:
    if record_count <= _MAX_SAMPLE:
        return 1
    return max(2, math.ceil(record_count * _HEADER_MIN_FRACTION))
