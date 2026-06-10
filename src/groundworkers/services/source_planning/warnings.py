"""Typed planning diagnostics.

These payloads exist so downstream callers do not need to parse free-form
strings to understand recoverable structural issues or hard planning failures.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(kw_only=True, frozen=True)
class PlanningWarning:
    """Recoverable planning issue that should remain visible to callers."""

    code: str
    message: str
    table_name: str | None = None
    column_name: str | None = None


@dataclass(kw_only=True, frozen=True)
class PlanningError:
    """Hard planning failure that should stop autonomous progression."""

    code: str
    message: str
    table_name: str | None = None
    column_name: str | None = None
