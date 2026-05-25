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
