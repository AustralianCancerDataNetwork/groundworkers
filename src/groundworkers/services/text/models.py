from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class NormalizeResult(BaseModel):
    """Result of a single-term normalization."""

    normalized: str
    original: str
    confidence: Literal["high", "medium", "low"]
    notes: str | None = None


class MappingCleanupResult(BaseModel):
    """Result of rewriting source text into a more mappable search phrase."""

    replacement: str
    original: str
    changed: bool
    confidence: Literal["high", "medium", "low"]
    notes: str | None = None


class DecomposeTerm(BaseModel):
    """One extracted clinical concept from a decomposition."""

    term: str
    domain_hint: str | None = None


class DecomposeResult(BaseModel):
    """Result of decomposing free text into a list of clinical search terms."""

    terms: list[DecomposeTerm]
    original: str


class Interpretation(BaseModel):
    """One candidate interpretation of an ambiguous term."""

    interpretation: str
    domain_hint: str | None = None
    context_clues: str | None = None


class DisambiguateResult(BaseModel):
    """Result of listing all plausible interpretations of an ambiguous term."""

    interpretations: list[Interpretation]
    original: str
    is_ambiguous: bool
