# History-of-Event Pattern

## The model

When a source item describes a patient's *history* of a condition, event, or
procedure, the correct OMOP representation is:

| Field | Value |
|---|---|
| Table | OBSERVATION |
| observation_concept_id | 4214956 (History of event) |
| value_as_concept_id | standard concept for the actual condition or event |

The entity concept is always 4214956. The clinical content goes in value_as_concept_id.
Do not populate CONDITION_OCCURRENCE for these items.

## Detection

Applies when the source label contains phrasing such as:
"history of", "h/o", "past history of", "prior history of", "previous history of",
"hx of", "Hx:", "prior diagnosis of".

## Family history

Family history follows the same structural pattern but uses a different entity concept:

| Field | Value |
|---|---|
| observation_concept_id | 4167217 (Family history of clinical finding) |
| value_as_concept_id | standard concept for the familial condition |

## What not to do

- Do not map "history of MI" to the myocardial infarction concept in
  CONDITION_OCCURRENCE. The distinction between current condition and reported
  history is clinically and analytically important.
- Do not leave value_as_concept_id empty. If the condition cannot be mapped,
  the item is unmappable — do not create a partial record with only the history
  entity concept.

## Downstream implications

CONDITION_OCCURRENCE is not populated for history-of items. Cohort definitions
and prevalence queries that rely on CONDITION_OCCURRENCE will not count these
records. Analysts querying "ever had condition X" must also search OBSERVATION
for history-of records if the source data uses history-of phrasing.
