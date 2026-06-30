from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: bool = True
    code: str
    message: str


class CandidateBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    domain: str | None = None
    vocabulary_id: str | None = None
    standard_only: bool = False
    active_only: bool = True
    include_synonyms: bool = True
    include_normalized: bool = True
    include_fulltext: bool = True
    include_embedding: bool = True
    include_standard_mappings: bool = True
    include_hierarchy_context: bool = False
    include_relationship_summary: bool = False
    parent_ids: list[int] | None = None
    per_channel_limit: int = Field(default=10, ge=1)
    overall_limit: int = Field(default=30, ge=1)
    model_name: str | None = None


class CandidateBundleConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str | None = None
    vocabulary_id: str | None = None
    standard_only: bool
    active_only: bool
    parent_ids: list[int] | None = None


class CandidateBundleChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    results: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_notes: list[str] = Field(default_factory=list)


class CandidateBundleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    constraints: CandidateBundleConstraints
    channels: dict[str, CandidateBundleChannel]
    standardized_candidates: list[dict[str, Any]] = Field(default_factory=list)
    candidate_union: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AssistedPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    filename: str | None = None
    caller_hint: str | None = None
    content_encoding: Literal["utf-8", "base64"] | None = "utf-8"
    include_intermediate: bool = False


class AssistedPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]
    detected_source_system: str | None = None
    raw_tables: list[dict[str, Any]] | None = None
    normalised_tables: list[dict[str, Any]] | None = None
    annotated_tables: list[dict[str, Any]] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: int
    llm_tier_used: bool


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
