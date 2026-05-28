from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class MapDataDictionaryRowInput(BaseModel):
    label: str = Field(
        ...,
        description="Human-readable label from the data dictionary (e.g. 'Systolic blood pressure', 'Primary diagnosis')",
    )
    vocabulary_id: str | None = Field(
        None,
        description=(
            "Source vocabulary identifier, if the row has an explicit code. "
            "Examples: ICD10CM, SNOMED, LOINC, RxNorm"
        ),
    )
    concept_code: str | None = Field(
        None,
        description="Source code, if available (e.g. 'E11.9', '271649006'). Requires vocabulary_id.",
    )
    domain: str | None = Field(
        None,
        description=(
            "Expected OMOP domain for this row. Providing this improves precision. "
            "Common values: Condition, Drug, Measurement, Procedure, Observation"
        ),
    )

    @model_validator(mode="after")
    def code_requires_vocabulary(self) -> "MapDataDictionaryRowInput":
        if self.concept_code and not self.vocabulary_id:
            raise ValueError("vocabulary_id is required when concept_code is provided")
        return self
