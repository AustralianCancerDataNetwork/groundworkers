# Standard Concept Selection

## Concepts must be active

Before selecting any standard concept as a mapping target, verify it is active:
- `INVALID_REASON IS NULL` (not deprecated, not updated, not deleted)
- `VALID_END_DATE = 2099-12-31` (OHDSI convention for currently active concepts)

Deprecated concepts (INVALID_REASON = 'D') and concepts that have been replaced
(INVALID_REASON = 'U') must not be used as mapping targets, even if they have
standard_concept = 'S'. For replaced concepts, follow the `Maps to` relationship
from the deprecated concept to find its active successor.

## Standard concepts are required for entity fields

A concept is standard when standard_concept = 'S' in the CONCEPT table.
All entity concept fields (condition_concept_id, measurement_concept_id,
observation_concept_id, drug_concept_id, etc.) must be populated with
standard concepts only.

Non-standard concepts belong in source_concept_id, not entity concept fields.

## Classification concepts are not valid mapping targets

Concepts with standard_concept = 'C' are classification nodes — they exist in
the hierarchy to support ancestor queries but are not intended as direct mapping
targets. If a candidate search returns a classification concept, look at its
standard descendants to find the appropriate specific concept.

## Vocabulary precedence by domain

When multiple vocabularies offer standard concepts for the same clinical entity,
prefer by domain:

**Condition**: SNOMED CT preferred. ICD-10CM/ICD-9CM are non-standard and require
a "Maps to" relationship to reach the standard SNOMED target.

**Drug**: RxNorm preferred. RxNorm Extension covers drugs not yet in core RxNorm;
use it only when no RxNorm equivalent exists for the same clinical drug.

**Measurement**: LOINC preferred for lab tests and clinical assessments with
established LOINC codes. SNOMED for findings without LOINC representation.

**Procedure**: SNOMED preferred for clinical EHR data. CPT4 preferred for US
procedural billing or claims context.

**Observation**: SNOMED preferred. LOINC for social and functional observations
that have LOINC panel/question codes.

## Zero concept

When no suitable standard concept exists, use concept_id = 0 (no matching concept).
Do not use a non-standard concept as a fallback for an entity concept field.
concept_id = 0 is the explicit signal that mapping was not possible; it is
preferable to a misleading non-standard concept.
