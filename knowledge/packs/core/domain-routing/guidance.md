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

## Scope: the entity concept only

This rule routes on the domain of the **entity** concept — the one going into
`measurement_concept_id`, `observation_concept_id`, `condition_concept_id`, etc.
It does not apply to `value_as_concept_id` or other subordinate value fields.
A value concept may legitimately come from a different domain than the record
it lives in — e.g. a Condition-domain concept (a plain diagnosis) used as
`value_as_concept_id` on an OBSERVATION row for a history-of or family-history
pattern (see `history-of-event`). Do not re-route a record to a different CDM
table because its value concept's domain differs from the entity concept's
domain — only the entity concept's domain decides the table.

## Unmapped source items (concept_id = 0)

When no valid standard concept mapping exists, set `*_concept_id = 0` (not NULL). The record still belongs in a CDM table — route using the best available domain signal (source vocabulary, source table, or caller context). Document the zero-mapping so it surfaces in data quality review.

Do not omit a record solely because no mapping was found. The source value is preserved in `*_source_value` and the zero-mapped record remains queryable.

## What not to do

- Do not route based on the source vocabulary (ICD codes are not always Condition; NDC codes are almost always Drug but verify the target concept's domain).
- Do not route based on the source table name or caller hint alone.
- Do not use concept_id = NULL — zero-mapping is the standard for unmapped concepts.
