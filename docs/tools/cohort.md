# Cohort Tools

One tool provides access to cohort concept reference data.  Requires `oa_cohorts` to
be enabled in the config.

## `cohort_find_concept_references`

Returns cohort definitions that reference a given OMOP concept ID.  Useful for
understanding where a concept is used across existing patient cohorts, providing
context to reviewers during a mapping session.

```json
{"concept_id": 4119419}
```

**Response** (when implemented):
```json
{
  "concept_id": 4119419,
  "cohort_references": [
    {"cohort_id": "...", "cohort_name": "...", "reference_type": "inclusion_criterion"}
  ]
}
```

!!! warning "Not yet implemented"
    `OaCohortAdapter` is currently a stub.  The tool is registered and returns
    `BACKEND_UNAVAIL` until the backing query is complete (Phase N of the development
    plan).  The tool is registered now so clients receive a structured error rather than
    an "unknown tool" failure.
