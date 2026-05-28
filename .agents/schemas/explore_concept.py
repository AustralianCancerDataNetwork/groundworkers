from __future__ import annotations

from pydantic import BaseModel, Field


class ExploreConceptInput(BaseModel):
    concept_id: int = Field(
        ...,
        ge=1,
        description="The OMOP concept_id to explore",
    )
    include_descendants: bool = Field(
        False,
        description=(
            "Whether to include descendant concepts. "
            "Useful when the candidate may be too broad and you want to see more specific options."
        ),
    )
