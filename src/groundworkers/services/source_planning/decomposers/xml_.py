"""Generic XML decomposer for repeating record elements."""

from __future__ import annotations

import math
import re
from collections import Counter
from xml.etree import ElementTree

from groundworkers.services.source_planning.models import RawTable, SourceFormat

_NS_RE = re.compile(r"\{[^}]*\}")
_MAX_SAMPLE = 5
_MAX_TABLES = 5
_MIN_RECORD_COUNT = 2
_MIN_COLUMN_COUNT = 2
_MAX_HEADERS = 120
_HEADER_MIN_FRACTION = 0.05
_VALUE_JOINER = " | "


def decompose(content: bytes, filename: str | None = None) -> list[RawTable]:
    """Parse XML bytes and return one ``RawTable`` per candidate record type."""

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return []

    candidates = _find_record_candidates(root)
    if not candidates:
        return []

    tables: list[RawTable] = []
    use_element_names = len(candidates) > 1
    for local_name, _ns_tag, true_count, collected in candidates:
        headers = _collect_headers(collected)
        if not headers:
            continue
        rows = [_element_to_row(elem, headers) for elem in collected]
        name = local_name if use_element_names else _table_name(filename, local_name)
        tables.append(
            RawTable(
                name=name,
                headers=headers,
                rows=rows,
                sample_rows=rows[:_MAX_SAMPLE],
                source_format=SourceFormat.XML,
                row_count=true_count,
                metadata={"xml_record_tag": local_name},
            )
        )
    return tables


def _strip_ns(tag: str) -> str:
    return _NS_RE.sub("", tag)


def _find_record_candidates(root: ElementTree.Element) -> list[tuple[str, str, int, list[ElementTree.Element]]]:
    counts: dict[str, tuple[str, int]] = {}
    collected: dict[str, list[ElementTree.Element]] = {}

    for elem in root.iter():
        if elem is root:
            continue
        local = _strip_ns(elem.tag)
        if local not in counts:
            counts[local] = (elem.tag, 0)
            collected[local] = []
        counts[local] = (counts[local][0], counts[local][1] + 1)
        collected[local].append(elem)

    def _avg_cols(local: str) -> float:
        elems = collected[local][:_MAX_SAMPLE]
        if not elems:
            return 0.0
        return sum(len(_flatten_element(elem)) for elem in elems) / len(elems)

    def _density(local: str) -> float:
        elems = collected[local][:_MAX_SAMPLE]
        if not elems:
            return 0.0
        flattened = [_flatten_element(elem) for elem in elems]
        headers = list(
            dict.fromkeys(path for row in flattened for path, values in row.items() if any(values))
        )
        if not headers:
            return 0.0
        non_empty = sum(
            1
            for row in flattened
            for header in headers
            if any(value for value in row.get(header, []))
        )
        return non_empty / (len(flattened) * len(headers))

    scores: dict[str, float] = {}
    for local, (_, count) in counts.items():
        if count < _MIN_RECORD_COUNT:
            continue
        avg = _avg_cols(local)
        if avg >= _MIN_COLUMN_COUNT:
            scores[local] = math.log2(count) * avg * (0.5 + _density(local))

    if not scores:
        return []

    top_locals = sorted(scores, key=scores.__getitem__, reverse=True)[:_MAX_TABLES]
    return [(local, counts[local][0], counts[local][1], collected[local]) for local in top_locals]


def _collect_headers(elements: list[ElementTree.Element]) -> list[str]:
    first_seen: dict[str, int] = {}
    counts: Counter[str] = Counter()
    for elem in elements:
        for path, values in _flatten_element(elem).items():
            if not any(values):
                continue
            if path not in first_seen:
                first_seen[path] = len(first_seen)
            counts[path] += 1
    if not counts:
        return []
    min_count = _minimum_header_count(len(elements))
    selected = [path for path, count in counts.items() if count >= min_count]
    if len(selected) > _MAX_HEADERS:
        selected = sorted(selected, key=lambda path: (-counts[path], first_seen[path]))[:_MAX_HEADERS]
    return sorted(selected, key=first_seen.__getitem__)


def _element_to_row(elem: ElementTree.Element, headers: list[str]) -> dict[str, str]:
    flattened = _flatten_element(elem)
    return {header: _VALUE_JOINER.join(value for value in flattened.get(header, []) if value) for header in headers}


def _table_name(filename: str | None, record_tag: str) -> str:
    if filename:
        base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." in base:
            base = base.rsplit(".", 1)[0]
        if base:
            return base
    return record_tag


def _flatten_element(
    elem: ElementTree.Element,
    prefix: str = "",
    flattened: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    if flattened is None:
        flattened = {}
    for attr, value in elem.attrib.items():
        _append_value(flattened, _join_path(prefix, f"@{_strip_ns(attr)}"), value or "")
    children = list(elem)
    text = (elem.text or "").strip()
    if not children:
        _append_value(flattened, prefix, text)
        return flattened
    if text:
        _append_value(flattened, _join_path(prefix, "#text"), text)
    for child in children:
        _flatten_element(child, _join_path(prefix, _strip_ns(child.tag)), flattened)
    return flattened


def _join_path(prefix: str, part: str) -> str:
    return part if not prefix else f"{prefix}.{part}"


def _append_value(flattened: dict[str, list[str]], path: str, value: str) -> None:
    if not path:
        return
    flattened.setdefault(path, []).append(value.strip())


def _minimum_header_count(element_count: int) -> int:
    if element_count <= _MAX_SAMPLE:
        return 1
    return max(2, math.ceil(element_count * _HEADER_MIN_FRACTION))
