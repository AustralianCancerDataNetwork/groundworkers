# OaCohorts Adapter

`OaCohortAdapter` provides access to OpenAnalytics cohort databases, enabling the
`cohort_find_concept_references` tool to report where OMOP concepts are used in
existing patient cohort definitions.

!!! warning "Not yet implemented"
    `OaCohortAdapter` is currently a stub containing only `__init__` and `close()`.
    The `find_concept_references` method is planned for a future phase.

## Planned functionality

When implemented, `find_concept_references(concept_id)` will query the cohort
database for all cohort definitions that reference the given OMOP concept ID as
an inclusion criterion, exclusion criterion, or outcome definition.

This data is surfaced to reviewers during a groundcrew mapping session to provide
clinical context — for example, showing that a proposed mapping concept is already
used in a validated oncology cohort adds confidence that it is the correct mapping.

## Configuration

```yaml
oa_cohorts:
  enabled: true
  db_url: "postgresql+psycopg://user:pass@localhost:5432/cohorts"
```

The adapter uses a SQLAlchemy session factory constructed from `db_url`.
`close()` calls `engine.dispose()` to release connection pool resources cleanly.
