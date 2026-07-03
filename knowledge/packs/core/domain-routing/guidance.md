# Domain Routing

## The fundamental rule

The `domain_id` of the **target standard concept** determines which CDM table a record is written to — not the domain implied by the source code, source vocabulary, or source table structure.

A source item coded as a procedure that maps to an RxNorm Ingredient goes to `DRUG_EXPOSURE`, not `PROCEDURE_OCCURRENCE`. A source item that looks like a diagnosis but maps to a LOINC measurement concept goes to `MEASUREMENT`. The target concept is authoritative.

## Domain → CDM table mapping

| domain_id | Primary CDM table |
|-----------|-------------------|
| Condition | CONDITION_OCCURRENCE |
| Drug | DRUG_EXPOSURE |
| Measurement | MEASUREMENT |
| Observation | OBSERVATION |
| Procedure | PROCEDURE_OCCURRENCE |
| Device | DEVICE_EXPOSURE |
| Specimen | SPECIMEN |
| Visit | VISIT_OCCURRENCE |
| Provider | PROVIDER |

## Unmapped source items (concept_id = 0)

When no valid standard concept mapping exists, set `*_concept_id = 0` (not NULL). The record still belongs in a CDM table — route using the best available domain signal (source vocabulary, source table, or caller context). Document the zero-mapping so it surfaces in data quality review.

Do not omit a record solely because no mapping was found. The source value is preserved in `*_source_value` and the zero-mapped record remains queryable.

## What not to do

- Do not route based on the source vocabulary (ICD codes are not always Condition; NDC codes are almost always Drug but verify the target concept's domain).
- Do not route based on the source table name or caller hint alone.
- Do not use concept_id = NULL — zero-mapping is the standard for unmapped concepts.
