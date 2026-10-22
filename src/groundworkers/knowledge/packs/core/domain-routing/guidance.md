# Domain Routing

## The fundamental rule

For an entity concept field, route the record using the `domain_id` of the
selected, active standard mapping target. Do not infer the destination table
from the source vocabulary, source code, source table, or caller hint when a
valid standard target is available.

For example, if a source item coded as a procedure maps to an active standard
Drug concept, the entity belongs in `DRUG_EXPOSURE`. If an item that looks like
a diagnosis maps to an active standard Measurement concept, it belongs in
`MEASUREMENT`. The selected entity target's domain determines the table.

Check that a proposed target is both standard (`standard_concept = 'S'`) and
active before using its domain for routing. A target that is retired, updated,
or a classification concept (`standard_concept = 'C'`) is not a valid entity
target.

If mapping produces multiple targets, or no valid standard target, do not
silently choose a table based on one candidate. Preserve the source item and
mapping evidence, flag the unresolved or multi-target decision, and use the
fallback rule below only when a record still needs to be written.

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

## Scope: entity concepts versus value concepts

This routing rule applies to the entity concept field — the concept that
identifies the record, such as `condition_concept_id`,
`measurement_concept_id`, or `observation_concept_id`.

It does not route on `value_as_concept_id` or another subordinate value field.
A value concept may have a different domain from the entity and the CDM table.
For example, personal history may be represented by the Observation-domain
`History of event` concept in `observation_concept_id`, with a Condition- or
Procedure-domain concept in `value_as_concept_id`. Family history may instead
use a pre-coordinated family-history concept in `observation_concept_id`.

Do not create a Condition or Procedure row, or change the table, solely because
a history value has that domain. Preserve the history, family relationship,
negation, and temporal meaning represented by the source and mapping pattern.

## Unmapped source items (concept_id = 0)

If no active standard target is available, use `*_concept_id = 0` rather than
NULL or an inactive/non-standard fallback. When a record must still be placed
in a CDM table, use the best available domain signal from the source vocabulary,
source table, and caller context. Treat that as a fallback decision, document
it, and preserve the original source value for review.

Do not omit a record solely because no mapping was found. The source value is preserved in `*_source_value` and the zero-mapped record remains queryable.

## What not to do

- Do not route based on the source vocabulary (ICD codes are not always Condition; NDC codes are almost always Drug but verify the target concept's domain).
- Do not route based on the source table name or caller hint alone.
- Do not use concept_id = NULL — zero-mapping is the standard for unmapped concepts.
