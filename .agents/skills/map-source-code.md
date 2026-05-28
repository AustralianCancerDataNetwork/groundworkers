# map-source-code

Map a source vocabulary code to its standard OMOP concept equivalent.

## When to use

Use this skill when you have an explicit vocabulary code — an ICD-10-CM
diagnosis code, a SNOMED CT concept, an RxNorm drug code, a LOINC
measurement — and want the standard OMOP concept_id it maps to.

This is more precise than `map-clinical-term` when you have the actual
source code rather than a label.

## Instructions

1. Call `concept_map_to_standard` with `vocabulary_id` and `concept_code`.
   Common `vocabulary_id` values: `ICD10CM`, `ICD10`, `ICD9CM`, `SNOMED`,
   `RxNorm`, `LOINC`, `CPT4`, `HCPCS`, `ATC`.

2. The result contains:
   - `source_concept` — the source concept record (may be non-standard)
   - `standard_concepts` — list of standard OMOP concepts it maps to via
     "Maps to" relationships
   - Each standard concept includes `concept_id`, `concept_name`,
     `domain_id`, `vocabulary_id`, `standard_concept`

3. If `standard_concepts` is empty, the source code exists in OMOP but has
   no "Maps to" mapping. Try `map-clinical-term` with the code's description
   as a fallback.

4. If the call returns `NOT_FOUND`, the code is not in the OMOP vocabulary
   loaded on this server. Check the `vocabulary_id` spelling, or use
   `map-clinical-term` with the label instead.

5. Use `explore-concept` on the returned `concept_id` if you want to
   understand the concept's position in the hierarchy before accepting it.
