"""Request/response models for deterministic semantic projection.

Mirrors the contract in agent-stack's agentive-design/_design/SEMANTIC_INTEGRATION/
03_groundworkers_mcp_contract.md. `context` carries whatever a definition needs
beyond the grounded concept itself:

- `raw_value` — the grounded field's own raw source code. Consulted by a
  `SpecialValuePolicy` (e.g. a "meets criteria for X" field, where the negative
  answer suppresses the row).
- `raw_source_fields` — a mapping of well-known slot name -> raw value, for
  definitions that resolve a row's slot from a *different* source field via a
  `DerivationRule` (e.g. a diagnosis's Primary/Contributing/Non-contributing
  role, collected in a sibling field).

Other context keys (`numeric_value`, `unit_concept_id`, `operator_concept_id`,
etc.) are accepted for forward compatibility with definitions not yet
implemented and are passed through under `source.*` without validation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SemanticProjectionRequest(BaseModel):
    """Input to `SemanticProjectionService.project()`."""

    model_config = ConfigDict(extra="forbid")

    grounded_concept_id: int
    grounded_domain: str
    grounded_concept_name: str | None = None
    source_text: str | None = None
    source_item_id: str | None = None
    definition_hint: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ProjectedRowModel(BaseModel):
    row_id: str
    table: str
    fields: dict[str, Any]


class ProjectionLinkModel(BaseModel):
    relationship_type: str
    source_row: str
    target_row: str


class SuppressedRowModel(BaseModel):
    row_id: str
    reason: str
    source_field: str
    source_code: str


class SemanticProjectionResult(BaseModel):
    """Deterministic projection outcome, transport-ready for the MCP response.

    `status` semantics:
    - `ok` — a definition matched and every row is fully bound.
    - `partial` — a definition matched but some row fields still need more
      context (see `unresolved_fields`).
    - `suppressed` — a definition matched but every row it would have produced
      was dropped by a `DerivationRule` or `SpecialValuePolicy` (see
      `suppressed_rows`) — nothing should be written for this item.
    - `no_match` — no definition matched (including an ambiguous match with no
      `definition_hint` to disambiguate).
    """

    definition_name: str | None
    role: str | None
    status: Literal["ok", "partial", "suppressed", "no_match"]
    rows: list[ProjectedRowModel] = Field(default_factory=list)
    links: list[ProjectionLinkModel] = Field(default_factory=list)
    constraint_checks: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_fields: list[dict[str, Any]] = Field(default_factory=list)
    suppressed_rows: list[SuppressedRowModel] = Field(default_factory=list)
    audit_notes: list[str] = Field(default_factory=list)
