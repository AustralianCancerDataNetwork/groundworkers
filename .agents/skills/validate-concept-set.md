# validate-concept-set

Validate an existing list of OMOP concept_ids — check each is standard and
active, flag non-standard concepts, and suggest standard replacements.

## When to use

Use this skill when you have a concept set (e.g. exported from ATLAS, pulled
from an existing study, or assembled by another skill) and want to check it
is ready for use in an OHDSI analysis.

## Instructions

### Step 1 — Fetch and classify each concept

For each `concept_id` in the input list, call `concept_get`.

Classify the result:

| `standard_concept` | `invalid_reason` | Classification |
|---|---|---|
| `'S'` | null | **valid** — keep |
| `'C'` | null | **classification** — used for grouping, not CDM tables; flag for review |
| null | null | **non-standard source** — must be replaced |
| any | `'D'` or `'U'` | **deprecated / updated** — must be replaced |
| — | error / not found | **missing** — concept_id not in vocabulary |

### Step 2 — Resolve non-standard and deprecated concepts

For each concept classified as non-standard or deprecated:

1. Call `concept_navigate_to_standard` with a list of the flagged concept_ids
   (batch call — pass all at once rather than one at a time).
2. For each result, `standard_concepts` contains the recommended replacement(s).
3. If `standard_concepts` is empty, the concept has no direct "Maps to"
   mapping — flag it as `unresolvable` for manual review.

### Step 3 — Produce the validation report

Return a structured summary:

- `valid_count` — concepts that are standard and active
- `flagged` — list of issues, each with:
  - `concept_id` — the original concept
  - `concept_name` — for readability
  - `issue` — one of: `non_standard`, `classification`, `deprecated`,
    `updated`, `missing`, `unresolvable`
  - `suggested_replacements` — list of standard concept_ids and names
    (empty if unresolvable or missing)
- `clean_concept_ids` — the valid concept_ids plus accepted replacements,
  ready to use as a revised concept set
- `needs_review` — true if any concepts are flagged as `unresolvable` or
  `missing`

## Notes

- Classification concepts (`standard_concept = 'C'`) are valid in the OMOP
  vocabulary hierarchy but should not appear in CDM fact tables. Whether to
  include or replace them depends on how the concept set is used (as a
  cohort criterion vs. a grouping/reporting hierarchy).
- A concept set that passes validation (all `S`, no deprecated) can still be
  clinically incomplete — this skill checks technical validity, not clinical
  coverage. Use `build-concept-set` or `explore-concept` to assess coverage.
