from __future__ import annotations

from pydantic import BaseModel, Field


class MapClinicalTermInput(BaseModel):
    term: str = Field(
        ...,
        description=(
            "The clinical term or label to map "
            "(e.g. 'type 2 diabetes mellitus', 'systolic blood pressure', 'paracetamol')"
        ),
    )
    domain: str | None = Field(
        None,
        description=(
            "Optional OMOP domain to restrict results. "
            "Common values: Condition, Drug, Measurement, Procedure, Observation"
        ),
    )
    limit: int = Field(
        5,
        ge=1,
        le=10,
        description="Maximum number of candidate concepts to return",
    )
