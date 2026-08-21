# OmopGraph Adapter

`OmopGraphAdapter` wraps [omop-graph](https://australiancancerdatanetwork.github.io/omop-graph/) as the backend runtime for graph-backed concept operations. It owns the dependency-shaped interface to `omop-graph`; caller-facing graph workflows live one layer up in `GraphService` and `ConceptGroundingService`.

## What it owns

The adapter is responsible for:

- building and probing the `omop-graph` runtime lazily
- normalizing raw concept and graph results into plain Python dicts and tuples
- translating backend failures into `GroundworkersError`
- exposing one-operation primitives that services can compose into higher-level workflows

It does not own caller-facing policy such as hierarchy-walk packaging, grounding tier selection, or REST/MCP response shaping.

## Primitive surface

| Method | Used by service(s) |
|---|---|
| `get_concept(concept_id)` | `GraphService` |
| `get_concept_by_code(vocab, code)` | `GraphService` |
| `concept_views(concept_ids)` | `GraphService` |
| `parents(concept_id)` / `children(concept_id)` | `GraphService` |
| `edges(concept_id, ...)` | `GraphService` |
| `shortest_paths(source_id, target_id, ...)` | `GraphService` |
| `traverse_neighborhood(concept_id, ...)` | `GraphService` |
| `run_ground_tier(resolvers, query, ...)` | `GraphService` grounding orchestration |
| `get_vocabulary_catalogue()` | `system_vocabulary_catalogue` |
| `canonicalize_domain(domain)` | `ConceptGroundingService` |
| `embedding_resolver_active` | `ConceptGroundingService` |
| `is_available()` | `system_status` |
| `probe()` | `system_status` |

`VocabService` uses `CDMAdapter` for lexical search operations and is separate from the omop-graph-backed graph service surface.

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

`standard_concept` is a boolean (`true`/`false`), not the raw `"S"` / `null` string stored in the OMOP CDM.

## Full-text search

`ConceptGroundingService` runs a tiered resolver pipeline through this adapter. The FullText tier uses PostgreSQL tsvector sidecar columns (`concept_name_tsvector`, `concept_synonym_name_tsvector`) when they are present on the vocabulary tables. Detection is automatic. When the sidecar columns are absent, the FullText tier returns no results and the pipeline falls through to later tiers.

`concept_search_fulltext` (via `VocabService`) uses the same sidecar columns and exposes `tsvector_available` so callers can detect degraded mode.

## Error handling

Adapter methods raise `GroundworkersError` on failure. Services propagate those errors unchanged; tools and REST routes translate them into transport-level responses.

The underlying `KnowledgeGraph` is built lazily on first use rather than at server startup. If the database or graph layer is unavailable, callers receive a clear `GroundworkersError` instead of an opaque lower-level exception.
