from __future__ import annotations

from pydantic import BaseModel, Field


class MapSourceCodeInput(BaseModel):
    vocabulary_id: str = Field(
        ...,
        description=(
            "The OMOP vocabulary identifier for the source code. "
            "Examples: ICD10CM, ICD10, ICD9CM, SNOMED, RxNorm, LOINC, CPT4, HCPCS, ATC"
        ),
    )
    concept_code: str = Field(
        ...,
        description="The source code to map (e.g. 'E11.9', '44054006', '272539003')",
    )
