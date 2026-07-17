# "Meets Criteria For..." Yes/No Gates

## The pattern

Source instruments frequently ask a dichotomous question that determines
whether a diagnosis, syndrome, or clinical state applies: "Does the
participant meet the criteria for dementia?", "Does the participant meet
criteria for MCI?", "Does the participant meet criteria for MBI?". These are
not ordinary categorical Yes/No observations — they are a gate on whether a
specific standard concept should be asserted for this participant at all.

## The model

- **Yes (1)**: emit one record for the underlying concept — CONDITION_OCCURRENCE
  when the concept's domain is Condition, OBSERVATION otherwise (per
  `domain-routing`).
- **No (0)**: emit nothing. Do not write a parallel OBSERVATION record coding
  the negative answer with a generic "No" value_as_concept_id.

This differs from an ordinary Yes/No categorical item (e.g. "does the
participant currently smoke?"), where both directions are independently
informative and both get recorded. The distinguishing feature of a criteria
gate is that a "No" answer only means "the named concept does not apply" —
it carries no positive clinical content of its own.

## The exception

Before defaulting to "No → nothing", check whether a standard concept exists
for the *negative* finding specifically (e.g. an explicit "criteria not met"
or "absence of X" concept distinct from a generic negation). This is
uncommon — most vocabularies do not pre-coordinate the negative case — but
when one exists, use it rather than suppressing the record.

## Detection

Look for phrasing such as "meet(s) criteria for", "meet(s) the criteria for",
"meets any criteria for" in the field label, where the field is a radio/Yes-No
item gating a specific diagnostic concept. Ordinary presence/absence
checkboxes and self-report Yes/No items that are not framed as a diagnostic
criteria determination are out of scope for this pattern — apply
`value-representation`'s normal Yes/No handling to those instead.

## What not to do

- Do not write a `value_as_concept_id`-coded "No" record for a criteria gate —
  it adds a row with no clinical content and implies a false symmetry between
  meeting and not meeting diagnostic criteria.
- Do not use this pattern for items where "No" is itself independently
  meaningful (e.g. a clinician confidence judgment, a symptom checklist item)
  — only apply it to fields that are literally asking whether the participant
  meets criteria for a named concept.

## See also

`diagnosis-role-modifier` often applies to the same syndromes downstream of a
criteria gate — once a diagnosis is asserted via a "Yes", a separate role/status
field may further qualify it.
