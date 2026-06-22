"""Canonical header lookup for source-planning classification.

This module is the one place where header-surface knowledge is centralized for
deterministic column classification. It is intentionally isolated so the rest
of the planning stack can stay generic and avoid source-specific drift.
"""

from __future__ import annotations

import re

from groundworkers.services.source_planning.models import ColumnRole

_Entry = tuple[ColumnRole, float, str | None, bool]

_SEP_RE = re.compile(r"[\s_\-]+")
_PIPELINE_META_PREFIXES: tuple[str, ...] = ("_raw_", "_elt_", "_meta_")


def _normalise(header: str) -> str:
    """Return a separator-insensitive canonical form for header lookup."""

    cleaned = header.strip().strip('"').strip("'").strip().lstrip("\ufeff").strip()
    return _SEP_RE.sub("", cleaned.lower())


_BUILTIN: dict[str, _Entry] = {
    "code": (ColumnRole.codes, 1.0, None, False),
    "label": (ColumnRole.label, 1.0, None, False),
    "name": (ColumnRole.label, 0.8, None, False),
    "description": (ColumnRole.description, 1.0, None, False),
    "notes": (ColumnRole.description, 0.8, None, False),
    "section": (ColumnRole.section, 1.0, None, False),
    "section_header": (ColumnRole.section, 1.0, None, False),
    "group_name": (ColumnRole.section, 1.0, None, False),
    "group": (ColumnRole.subsection, 0.9, None, False),
    "category": (ColumnRole.section, 0.9, None, False),
    "module": (ColumnRole.section, 0.9, None, False),
    "dataset": (ColumnRole.subsection, 0.9, None, False),
    "table_name": (ColumnRole.section, 1.0, None, False),
    "column_name": (ColumnRole.label, 1.0, None, False),
    "variable_name": (ColumnRole.attribute, 1.0, None, False),
    "variable / field name": (ColumnRole.attribute, 1.0, None, False),
    "field_name": (ColumnRole.attribute, 1.0, None, False),
    "field_label": (ColumnRole.label, 1.0, None, False),
    "field_note": (ColumnRole.description, 0.9, None, False),
    "field_annotation": (ColumnRole.annotation, 0.9, None, False),
    "field_type": (ColumnRole.field_type_ctrl, 1.0, None, False),
    "form_name": (ColumnRole.section, 0.95, None, False),
    "element_label": (ColumnRole.label, 1.0, None, False),
    "element_enum": (ColumnRole.values, 1.0, None, True),
    "element_note": (ColumnRole.description, 0.9, None, False),
    "element_type": (ColumnRole.field_type_ctrl, 1.0, None, False),
    "attribute_name": (ColumnRole.label, 1.0, None, False),
    "key_element_name": (ColumnRole.label, 1.0, None, False),
    "preferred_label": (ColumnRole.label, 1.0, None, False),
    "preferred_term": (ColumnRole.label, 1.0, None, False),
    "pref_label": (ColumnRole.label, 1.0, None, False),
    "alt_label": (ColumnRole.annotation, 0.9, None, False),
    "permitted_values": (ColumnRole.values, 0.9, None, True),
    "standard_values_list": (ColumnRole.values, 1.0, None, True),
    "choices, calculations, or slider labels": (ColumnRole.values, 1.0, None, True),
    "system": (ColumnRole.source_vocab, 1.0, None, False),
    "coded_system": (ColumnRole.source_vocab, 1.0, None, False),
    "vocabulary": (ColumnRole.source_vocab, 1.0, None, False),
    "vocab": (ColumnRole.source_vocab, 1.0, None, False),
    "data_type": (ColumnRole.data_type, 1.0, None, False),
    "is_pk": (ColumnRole.local_pk, 1.0, None, False),
    "is_fk": (ColumnRole.irrelevant, 1.0, None, False),
    "fk_table": (ColumnRole.mapping_context, 1.0, None, False),
    "fk_column": (ColumnRole.mapping_context, 1.0, None, False),
    "variable_code": (ColumnRole.local_pk, 0.9, None, False),
    "durable_key": (ColumnRole.local_pk, 0.9, None, False),
    "drg_code": (ColumnRole.codes, 1.0, "DRG", False),
    "icd10_code": (ColumnRole.codes, 1.0, "ICD10CM", False),
    "icd9_code": (ColumnRole.codes, 1.0, "ICD9CM", False),
    "snomed_code": (ColumnRole.codes, 1.0, "SNOMED", False),
    "loinc_code": (ColumnRole.codes, 1.0, "LOINC", False),
    "cpt_code": (ColumnRole.codes, 1.0, "CPT4", False),
    "hcpcs_code": (ColumnRole.codes, 1.0, "HCPCS", False),
    "rxnorm_code": (ColumnRole.codes, 1.0, "RxNorm", False),
    "ndc_code":    (ColumnRole.codes, 1.0, "NDC",    False),
    "sctid":       (ColumnRole.codes,    1.0, "SNOMED", False),
    "ncitc":       (ColumnRole.codes,    1.0, "NCIT",   False),
    # O3 / oncology data dictionary column shapes
    "valuename":   (ColumnRole.label,    1.0, None,     False),
    "definition":  (ColumnRole.description, 1.0, None,  False),
    "stringcode":  (ColumnRole.local_pk, 1.0, None,     False),
    "numericcode": (ColumnRole.local_pk, 0.9, None,     False),
}

_MERGED = {_normalise(key): value for key, value in _BUILTIN.items()}


def builtin_catalogue() -> dict[str, dict[str, object]]:
    """Return the authoritative Tier A canonical header catalogue.

    This exposes the recognized header surfaces and their deterministic
    annotation metadata in a JSON-friendly shape for inspection tools and
    caller-facing resources.
    """

    return {
        header: {
            "role": role.value,
            "confidence": confidence,
            "inferred_vocab": inferred_vocab,
            "packed_value": packed_value,
        }
        for header, (role, confidence, inferred_vocab, packed_value) in _BUILTIN.items()
    }


def lookup(header: str) -> _Entry | None:
    """Return the canonical annotation for ``header`` if one is known."""

    raw_lower = header.strip().lower()
    for prefix in _PIPELINE_META_PREFIXES:
        if raw_lower.startswith(prefix):
            return (ColumnRole.pipeline_meta, 1.0, None, False)

    entry = _MERGED.get(_normalise(header))
    if entry is not None:
        return entry

    for candidate in _path_lookup_candidates(header):
        entry = _MERGED.get(_normalise(candidate))
        if entry is not None:
            return entry
    return None


def _path_lookup_candidates(header: str) -> list[str]:
    """Return likely canonical suffixes for dotted XML path headers."""

    parts = [_clean_path_part(part) for part in header.split(".") if _clean_path_part(part)]
    if not parts:
        return []

    candidates: list[str] = []
    leaf = parts[-1]
    if len(parts) >= 2:
        candidates.append(f"{parts[-2]}_{leaf}")
    if leaf == "text" and len(parts) >= 2:
        candidates.append(parts[-2])
        if len(parts) >= 3:
            candidates.append(f"{parts[-3]}_{parts[-2]}")
    candidates.append(leaf)
    return list(dict.fromkeys(candidates))


def _clean_path_part(part: str) -> str:
    return part.strip().lstrip("@").lstrip("#")
