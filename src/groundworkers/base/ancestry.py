"""Validated ancestry request and MCP response contracts; no database policy."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from groundworkers.base.errors import ErrorCode

PositiveConceptId = Annotated[int, Field(strict=True, gt=0)]
ConceptIds = Annotated[list[PositiveConceptId], Field(min_length=2, max_length=200)]
AncestorDepth = Annotated[int, Field(strict=True, ge=0, le=20)]
AncestorLimit = Annotated[int, Field(strict=True, ge=1, le=50)]
AncestorOffset = Annotated[int, Field(strict=True, ge=0)]
ConstraintIds = Annotated[list[Annotated[str, Field(strict=True)]], Field(min_length=1)]


def validate_ancestor_depth(value: int) -> int:
    return TypeAdapter(AncestorDepth).validate_python(value)


def validate_ancestor_limit(value: int) -> int:
    return TypeAdapter(AncestorLimit).validate_python(value)


class SharedAncestorRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    concept_ids: ConceptIds
    max_depth: AncestorDepth = 5
    concept_class_ids: ConstraintIds | None = None
    vocabulary_ids: ConstraintIds | None = None
    limit: AncestorLimit = 10
    offset: AncestorOffset = 0

    @field_validator("concept_class_ids", "vocabulary_ids")
    @classmethod
    def normalize_constraints(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if any(not value.strip() for value in values):
            raise ValueError("constraint identifiers must not be blank")
        return list(dict.fromkeys(value.strip() for value in values))

    @model_validator(mode="after")
    def distinct_inputs(self):
        self.concept_ids = list(dict.fromkeys(self.concept_ids))
        if len(self.concept_ids) < 2:
            raise ValueError("at least two distinct concept IDs are required")
        return self


class AncestorConcept(BaseModel):
    concept_id: PositiveConceptId
    concept_name: str
    concept_code: str | None = None
    vocabulary_id: str
    domain_id: str
    concept_class_id: str
    standard_concept: Literal[True]
    is_active: Literal[True]
    depth: Annotated[int, Field(ge=0)]


class AncestorConstraints(BaseModel):
    concept_class_ids: list[str] | None
    vocabulary_ids: list[str] | None
    standard_only: Literal[True] = True
    active_only: Literal[True] = True
    same_domain: Literal[True] = True


class AncestorPage(BaseModel):
    offset: AncestorOffset
    limit: AncestorLimit
    returned: Annotated[int, Field(ge=0)]
    has_more: bool


class SharedAncestorData(BaseModel):
    status: Literal["selected", "ambiguous", "no_match", "incompatible_inputs"]
    found: bool
    input_concept_ids: ConceptIds
    input_domains: dict[str, str | None]
    constraints: AncestorConstraints
    ancestry_source: Literal["concept_ancestor"] = "concept_ancestor"
    distance_metric: Literal["min_levels_of_separation"] = "min_levels_of_separation"
    max_depth: AncestorDepth
    include_self: Literal[True] = True
    selection_complete: Literal[True] = True
    selection_reason: str
    selected_ancestor: AncestorConcept | None
    selected_distances_by_input: dict[str, Annotated[int, Field(ge=0)]]
    common_ancestors: list[AncestorConcept]
    common_candidate_count: Annotated[int, Field(ge=0)]
    nearest_candidate_count: Annotated[int, Field(ge=0)]
    page: AncestorPage

    @model_validator(mode="after")
    def consistent_selection(self):
        selected = self.status == "selected"
        if self.found != selected or (self.selected_ancestor is not None) != selected:
            raise ValueError("status, found and selected_ancestor disagree")
        if selected and self.nearest_candidate_count != 1:
            raise ValueError("selected outcome must have one nearest candidate")
        if self.status == "ambiguous" and self.nearest_candidate_count < 2:
            raise ValueError("ambiguous outcome must retain a nearest-distance tie")
        if (
            self.status in ("no_match", "incompatible_inputs")
            and self.common_candidate_count
        ):
            raise ValueError("nonmatching outcome cannot contain common candidates")
        if (
            self.page.returned != len(self.common_ancestors)
            or self.page.returned > self.page.limit
        ):
            raise ValueError("page count disagrees with returned candidates")
        if self.nearest_candidate_count > self.common_candidate_count:
            raise ValueError("nearest count exceeds common candidate count")
        if set(self.input_domains) != {str(cid) for cid in self.input_concept_ids}:
            raise ValueError("domain evidence must cover every input")
        if selected and self.selected_ancestor is not None:
            distances = self.selected_distances_by_input
            if set(distances) != set(self.input_domains):
                raise ValueError("selected ancestor distances must cover every input")
            if (
                max(distances.values()) != self.selected_ancestor.depth
                or max(distances.values()) > self.max_depth
            ):
                raise ValueError(
                    "selected ancestor distance disagrees with input evidence"
                )
        elif self.selected_distances_by_input:
            raise ValueError("unselected outcome must not carry selected distances")
        return self


class SharedAncestorResponse(BaseModel):
    error: bool = False
    code: ErrorCode | None = None
    message: str | None = None
    data: SharedAncestorData | None = None

    @model_validator(mode="after")
    def consistent_envelope(self):
        if self.error:
            if not self.code or not self.message or self.data is not None:
                raise ValueError("error response requires code/message and no data")
        elif self.data is None or self.code is not None or self.message is not None:
            raise ValueError("success response requires data and no error fields")
        return self
