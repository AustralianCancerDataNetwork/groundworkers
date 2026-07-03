# OmopGraph Adapter

`OmopGraphAdapter` wraps [omop-graph](https://australiancancerdatanetwork.github.io/omop-graph/)
as the backend runtime for graph-backed concept operations. Direct Python callers
normally use `GraphService`, `ConceptGroundingService`, or `MappingService`; the
adapter exists so those services can share one backend wrapper.

## Key methods

| Method | Used by service(s) |
|---|---|
| `get_concept(concept_id)` | `GraphService` |
| `get_concept_by_code(vocab, code)` | `GraphService` |
| `ground_with_plan(request)` | `ConceptGroundingService` via `GraphService` |
| `get_ancestors(concept_id, max_depth)` | `GraphService` |
| `get_descendants(concept_id, max_depth)` | `GraphService` |
| `get_edges(concept_id)` | `GraphService` |
| `get_neighbors(concept_id, ...)` | `GraphService` |
| `find_equivalency_path(source_id, target_id, ...)` | `GraphService` |
| `find_path(source_id, target_id, max_depth, ...)` | `GraphService` |
| `map_to_standard(vocab, code)` | `GraphService` |
| `get_vocabulary_catalogue()` | `system_vocabulary_catalogue` |
| `is_available()` | `system_status` |

`VocabService` uses `CDMAdapter` for lexical search operations and is separate from
the omop-graph-backed graph service surface.

## Concept response shape

All methods that return concept data use a consistent dict shape:

```json
{
  "concept_id": 4119419,
  "concept_name": "Malignant neoplasm of bronchus and lung",
  "concept_code": "363358000",
  "vocabulary_id": "SNOMED",
  "domain_id": "Condition",
  "concept_class_id": "Clinical Finding",
  "standard_concept": true,
  "valid_start_date": "2002-01-31",
  "valid_end_date": "2099-12-31",
  "invalid_reason": null
}
```

`standard_concept` is a boolean (`true`/`false`), not the raw `"S"` / `null` string
stored in the OMOP CDM.

## Full-text search

`ConceptGroundingService` runs a tiered resolver pipeline through this adapter's
backend runtime. The FullText tier uses PostgreSQL tsvector sidecar columns
(`concept_name_tsvector`, `concept_synonym_name_tsvector`) when they are present
on the vocabulary tables. Detection is automatic. When the sidecar columns are
absent, the FullText tier returns no results and the pipeline falls through to
later tiers.

`concept_search_fulltext` (via `VocabService`) uses the same sidecar columns and
exposes `tsvector_available` so callers can detect degraded mode.

## Error handling

All adapter methods raise `GroundworkersError` on failure. Tools or REST routes
translate those into transport-level error responses.

The underlying `KnowledgeGraph` is built lazily on first use rather than at
server startup. If the database or graph layer is unavailable, callers receive a
clear `GroundworkersError` instead of an opaque lower-level exception.
