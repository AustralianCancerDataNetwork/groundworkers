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

## Verify both ends of a mapping

Before using a relationship to select an entity target, check the target
concept itself:

- It must be standard (`standard_concept = 'S'`), not a classification concept.
- It must be active (`invalid_reason IS NULL` and
  `valid_end_date = 2099-12-31`).

A valid relationship row does not guarantee a valid target concept. In
particular, `valid = true` describes the relationship row; it does not mean
the target concept is active or standard. If a `Maps to` target is retired or
updated, follow the replacement only when an active, standard successor is
available. Otherwise preserve the source and flag the mapping for review.

When a source maps to multiple targets, check whether the mapping is
one-to-many by design. Preserve all supported targets and their roles; do not
silently collapse them into one target or treat them as interchangeable.

## Interpret relationship types narrowly

Use each relationship only for the claim it supports:

- `Maps to` identifies a mapping target. Verify that target before using it.
- `Maps to value` supplies a value for a mapped entity; it does not replace
  the entity target or determine the record's CDM table.
- `Is a` expresses hierarchy. It may support ancestor or descendant queries,
  but does not establish an exact mapping or equivalence.
- Attribute relationships describe properties of a concept. They do not, by
  themselves, establish identity or a mapping.
- Equivalence-like or informational relationships such as `eq`, `cat`, and
  `poss_eq` are evidence to inspect, not mapping instructions. Check their
  meaning and specificity before using them.

Do not treat metadata relationships as clinical meaning. For example,
`Has status` describes SNOMED definition status, and `Has Module` identifies
a module; neither is a clinical attribute or an identity path.

## Preserve source meaning and qualifiers

Keep the original source code and source value available when selecting or
reviewing a target. A mapping may lose detail that matters for analysis; do
not assume that laterality, approach, timing, status, negation, or other
qualifiers survive unless the target explicitly represents them.

For one-to-many mappings, preserve the role of each target. Do not assume the
targets are interchangeable or that they mean the source concepts occurred
together.

For disjunctive source items such as “A or B”, do not map to both A and B as
though both occurred. Prefer a target that preserves the disjunction; if none
is available, use the least specific faithful target or flag the item for
review.

Do not assume hierarchy-based concept sets preserve source meaning across
vocabularies. Check the relevant vocabulary and hierarchy before relying on
ancestors or descendants; where coverage or placement is unreliable, retain
the source code and document the limitation.

Preserve historical, family-history, negated, and status concepts as such.
Do not turn them into current diagnoses just because a related disease or
procedure appears as a target or value.

## Vocabulary preference is conditional

Treat vocabulary preference as a tie-breaker between active standard concepts
that represent the same meaning at the required level of specificity. It is
not a rule to translate every source code into one preferred vocabulary.

First establish what the source item represents, the target field it will
populate, and the intended use. Then verify candidate meaning, domain,
standardness, and active status. Preserve an appropriate active standard
source concept when it already represents the recorded item. Do not prefer a
candidate from another vocabulary solely because its vocabulary is listed
below.

### Condition

SNOMED is the main clinical Condition backbone. ICD10CM, ICD9CM, and ICD10
Condition concepts are generally non-standard and require an active `Maps to`
target. Check one-to-many mappings and preserve source detail that the targets
do not retain.

ICDO3 histology-by-site combinations are standard Condition concepts in their
own right. Do not replace them automatically with SNOMED or assume their
hierarchy reliably captures histology or behaviour.

### Procedure

Do not default to SNOMED. CPT4, HCPCS, ICD10PCS, and ICD9Proc can contain active
standard Procedure concepts. Preserve the source standard concept when it is
the appropriate representation for the task, especially for claims or
administrative data. Use SNOMED when it is the appropriate clinical
representation, not as an automatic substitute.

Do not assume a hierarchy or an informational equivalence link proves an exact
mapping. In particular, verify that targets are active and treat
ICD10PCS-to-SNOMED ancestry as potentially incomplete or misleading.

### Measurement and Observation

Choose based on what was recorded: an instrument, an administered procedure,
a question or survey item, or a score. Do not assume that concepts for the same
instrument in different vocabularies share a domain or an exact crosswalk.
For example, LOINC survey items are in Observation, while SNOMED assessment
scales are in Measurement. Keep instrument identity, administration, and score
distinct where the source data distinguishes them.

### Other domains

Keep the existing Drug and other domain preferences as provisional defaults.
Apply them only when candidates are active and equivalent at the required
precision; revisit them when domain-specific audit evidence is available.

## Zero concept

When no suitable standard concept exists, use concept_id = 0 (no matching concept).
Do not use a non-standard concept as a fallback for an entity concept field.
concept_id = 0 is the explicit signal that mapping was not possible; it is
preferable to a misleading non-standard concept.
