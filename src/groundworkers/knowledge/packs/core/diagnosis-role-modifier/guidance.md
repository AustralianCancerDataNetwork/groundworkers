# Diagnosis + Role/Status Modifier

## The pattern

Some source instruments split a single clinical fact across two fields:

1. A **presence field** — does the participant have this diagnosis/finding at all?
2. A **role/status field** — what is its status or role relative to the participant's
   overall clinical picture (primary, contributing, non-contributing; primary vs.
   secondary; admitting vs. discharge; provisional vs. confirmed)?

This is a two-source, one-target mapping: both fields describe the *same*
CONDITION_OCCURRENCE record, not two independent facts. Do not emit a second,
freestanding OBSERVATION record to carry the role — populate
`CONDITION_OCCURRENCE.condition_status_concept_id` on the same record instead.

## Detection

Look for a presence/diagnosis field paired with a sibling field whose label reads
as a status, role, or type qualifier of that same diagnosis (often suffixed or
named similarly — e.g. `xdx` + `xdxif`, `xdx` + `xdx_role`). The pairing is
usually confirmed by the branching logic or field note referencing the diagnosis
field, and by choice lists such as "Primary / Contributing / Non-contributing" or
"Primary / Secondary".

## The model

| Field | Value |
|---|---|
| Table | CONDITION_OCCURRENCE |
| condition_concept_id | standard concept for the diagnosis (from the presence field) |
| condition_status_concept_id | standard concept from the **Condition Status** vocabulary, selected using the role field's value |

`Condition Status` is its own OMOP vocabulary/domain. Commonly available concepts include:

| concept_id | concept_name |
|---|---|
| 32902 | Primary diagnosis |
| 32908 | Secondary diagnosis |
| 32890 | Admission diagnosis |
| 32896 | Discharge diagnosis |
| 32899 | Preliminary diagnosis |
| 32893 | Confirmed diagnosis |

There is no dedicated "Contributing diagnosis" concept — map a "contributing"
role to **Secondary diagnosis (32908)**, the closest available status, and note
the approximation in the mapping's notes/comments.

## Non-contributing (and other null-role values)

A role value that means "not actually relevant/contributing" (e.g.
"Non-contributing") is not a status to record — it means the diagnosis should
not be asserted at all for this record. **Suppress the CONDITION_OCCURRENCE
record entirely** when the role field carries this value, rather than writing
a record with an empty or misleading condition_status_concept_id.

## What not to do

- Do not create a second OBSERVATION row (concept_id=0 or otherwise) just to
  carry the role/status value as free text or a generic categorical code —
  that treats a modifier of the diagnosis as an independent clinical fact.
- Do not populate condition_status_concept_id with a source-local code; always
  resolve it to a standard Condition Status concept.
- Do not assert the CONDITION_OCCURRENCE record when the paired role field
  indicates the diagnosis is non-contributing/not applicable.

## Output structure: representing the merge in a row-per-source-field ETL format

When the mapping output is authored as one row per source field, this pattern
is an n:1 merge (two source fields, one CDM record) and needs an explicit way
to say "this record's condition_status_concept_id comes from a second field."
The convention used in this pipeline is three additional columns on the
presence field's row:

- `secondary_source_field` — the source field supplying the role/status value
- `secondary_target_column` — the CDM column it populates (`condition_status_concept_id`)
- `secondary_code_map` — `source_code:concept_id` pairs, pipe-separated, with the
  literal token `SUPPRESS` for any code that should drop the record
  (e.g. `1:32902|2:32908|3:SUPPRESS`)

The role field itself gets no row of its own — it is consumed entirely by the
presence field's row. This same three-column mechanism generalizes to other
n:1 merges beyond diagnosis role (e.g. a presence flag paired with a severity
or laterality modifier), not just condition status.

## See also

`pre-coordinated-split` covers the related but distinct case where a *single*
source concept's vocabulary relationships decompose into an entity + value —
this pack covers two *separate* source fields describing one record.
