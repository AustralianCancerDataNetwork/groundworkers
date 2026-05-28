from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class FindSemanticNeighboursInput(BaseModel):
    query: str | None = Field(
        None,
        description="Free-text term to search for semantically similar concepts",
    )
    concept_id: int | None = Field(
        None,
        ge=1,
        description="Known OMOP concept_id to find nearest embedding-space neighbours for",
    )
    domain: str | None = Field(
        None,
        description="Optional OMOP domain filter (e.g. Condition, Drug, Measurement)",
    )
    standard_only: bool = Field(
        True,
        description="Restrict results to standard concepts only (recommended for concept set building)",
    )
    limit: int = Field(
        10,
        ge=1,
        le=50,
        description="Maximum number of neighbours to return",
    )

    @model_validator(mode="after")
    def requires_query_or_concept_id(self) -> "FindSemanticNeighboursInput":
        if not self.query and not self.concept_id:
            raise ValueError("At least one of query or concept_id must be provided")
        return self
