from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DetailResult(BaseModel):
    resource_id: str
    item: dict[str, Any] | None


class ListResult(BaseModel):
    resource_id: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class SearchHit(BaseModel):
    id: str | int
    score: float
    payload: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    resource_id: str
    query: str | None = None
    items: list[SearchHit] = Field(default_factory=list)
    limit: int = 10


class DatasetStatus(BaseModel):
    module: str
    enabled: bool
    resources: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def enum_value(value: object) -> str | None:
    """Render an enum (or a plain value) as the string a result payload carries.

    Backends hand back either an enum member or an already-plain value
    depending on version and code path, and MCP payloads must be JSON-safe
    either way. ``None`` passes through so an unset field stays unset rather
    than becoming the string ``"None"``.
    """
    if value is None:
        return None
    return str(getattr(value, "value", value))


def required_enum_value(value: object) -> str:
    """``enum_value`` for a source that cannot be null.

    Raises rather than returning ``None`` so a genuinely absent value fails
    loudly. The previous per-module copies stringified ``None`` into the literal
    text ``"None"``, which reached the operator as if it were a real value.
    """
    rendered = enum_value(value)
    if rendered is None:
        raise ValueError("Expected a value for a non-nullable enum field, got None.")
    return rendered
