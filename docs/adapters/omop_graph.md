# OmopGraph Adapter

`OmopGraphAdapter` wraps [omop-graph](https://australiancancerdatanetwork.github.io/omop-graph/)
to provide concept lookup, full-text search, hierarchy traversal, path finding, and
vocabulary catalogue queries.  It is the backing adapter for all concept, resolver,
and search tools, plus both system tools.

## Key methods

| Method | Used by tool(s) |
|---|---|
| `get_concept(concept_id)` | `concept_get` |
| `get_concept_by_code(vocab, code)` | `concept_by_code` |
| `ground(query, ...)` | `concept_ground` |
| `get_ancestors(concept_id, max_depth)` | `concept_ancestors` |
| `get_descendants(concept_id, max_depth)` | `concept_descendants` |
| `get_edges(concept_id)` | `concept_relationships` |
| `find_equivalency_path(source_id, target_id, ...)` | `concept_equivalency_path` |
| `find_path(source_id, target_id, max_depth, ...)` | `concept_path` |
| `map_to_standard(vocab, code)` | `concept_map_to_standard` |
| `get_vocabulary_catalogue()` | `system_vocabulary_catalogue` |
| `is_available()` | `system_status` |

`OmopVocabAdapter` shares the same database engine and backs the search tools
(`concept_search_exact`, `concept_search_fulltext`, `concept_navigate_to_standard`).

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

`concept_ground` runs a tiered resolver pipeline.  The FullText tier uses PostgreSQL
tsvector sidecar columns (`concept_name_tsvector`, `concept_synonym_name_tsvector`)
when they are present on the vocabulary tables.  Detection is automatic — no
configuration is required.  When the sidecar columns are absent, the FullText tier
returns no results and the pipeline falls through to Partial matching.

`concept_search_fulltext` (via `OmopVocabAdapter`) uses the same sidecar columns and
exposes `tsvector_available` in its response so callers can detect degraded mode.

## Error handling

All adapter methods raise `GroundworkersError` on failure.  Tools catch these and convert
them to the structured error dict format before returning to the MCP client.

Lazy KnowledgeGraph initialisation — the `KnowledgeGraph` object is constructed on
first use, not at server startup.  A database connectivity check is performed before
construction; failure raises `GroundworkersError("DB_UNAVAILABLE", ...)` with a clear
message rather than an opaque SQLAlchemy exception.
