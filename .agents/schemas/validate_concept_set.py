from __future__ import annotations

from pydantic import BaseModel, Field


class ValidateConceptSetInput(BaseModel):
    concept_ids: list[int] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of OMOP concept_ids to validate",
    )
    include_classification_concepts: bool = Field(
        False,
        description=(
            "When True, classification concepts (standard_concept='C') are treated as "
            "valid and included in clean_concept_ids. When False (default), they are "
            "flagged for review."
        ),
    )
