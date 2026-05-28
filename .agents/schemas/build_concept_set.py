from __future__ import annotations

from pydantic import BaseModel, Field


class BuildConceptSetInput(BaseModel):
    term: str = Field(
        ...,
        description="Clinical anchor term to ground and expand (e.g. 'lung cancer', 'metformin', 'HbA1c')",
    )
    domain: str | None = Field(
        None,
        description=(
            "Optional OMOP domain to restrict grounding. "
            "Common values: Condition, Drug, Measurement, Procedure, Observation"
        ),
    )
    max_depth: int = Field(
        3,
        ge=1,
        le=10,
        description=(
            "How many levels down the hierarchy to expand from the anchor. "
            "3–5 is appropriate for most conditions and drug classes."
        ),
    )
    vocabulary_id: str | None = Field(
        None,
        description="Optional vocabulary filter for grounding (e.g. 'SNOMED', 'RxNorm')",
    )
