from __future__ import annotations

import base64
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from groundworkers.services.source_planning.models import PreIngestBundle


def decode_content(content: str, content_encoding: str | None) -> bytes:
    """Decode submitted source content from the transport payload."""

    if content_encoding in {None, "utf-8"}:
        return content.encode("utf-8")
    if content_encoding == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except Exception as exc:
            raise ValueError(
                "content_encoding is 'base64' but content is not valid base64"
            ) from exc
    raise ValueError(
        f"Unknown content_encoding {content_encoding!r}. Use 'utf-8' or 'base64'."
    )


def serialize_pre_ingest_bundle(
    bundle: PreIngestBundle,
    *,
    include_intermediate: bool = False,
) -> dict[str, Any]:
    """Return a JSON-safe view of a ``PreIngestBundle``."""

    return {
        "plan": _jsonable(bundle.plan),
        "detected_source_system": bundle.detected_source_system,
        "structural_skip_field_types": list(bundle.structural_skip_field_types),
        "packed_value_column_hint": bundle.packed_value_column_hint,
        "raw_tables": _jsonable(bundle.raw_tables) if include_intermediate else None,
        "normalised_tables": _jsonable(bundle.normalised_tables) if include_intermediate else None,
        "annotated_tables": _jsonable(bundle.annotated_tables) if include_intermediate else None,
        "warnings": _jsonable(bundle.warnings),
        "errors": _jsonable(bundle.errors),
        "elapsed_ms": bundle.elapsed_ms,
        "llm_tier_used": bundle.llm_tier_used,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
