"""Provenance records for structural source normalization.

These records explain *how representation changed* during normalization.
They do not assign OMOP meaning and they do not imply caller-facing policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(kw_only=True, frozen=True)
class HeaderProvenance:
    """Column-level record of structural cleanup performed during normalization.

    Attributes
    ----------
    original:
        The raw header surface emitted by decomposition.
    normalised:
        The final structural header surface used by downstream semantic stages.
    operations:
        Ordered normalization steps applied to reach ``normalised``.
    """

    original: str
    normalised: str
    operations: list[str] = field(default_factory=list)
