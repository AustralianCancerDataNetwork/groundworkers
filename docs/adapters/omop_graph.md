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

## Temporary stored-ancestry primitive and upstream migration

Groundworkers temporarily provides `q_ancestor_rows()` in
`adapters/_omop_graph_ancestry.py` pending an omop-graph release. It depends only
on SQLAlchemy and the OMOP model; it contains no Groundworkers selection policy.
`OmopGraphAdapter.ancestor_rows()` is the stable execution boundary. It returns
plain rows with descendant ID, ancestor ID and stored minimum/maximum separation.
Distinct zero-separation ancestors are retained; `include_self` compares IDs.
`max_separation=0` is supported and empty input returns no rows.

The shared-ancestor service uses `GraphService.get_stored_ancestor_maps()` to batch
ancestry and strict metadata lookup, adding self at depth zero. Ordinary parent
and descendant walks retain their existing semantics. Eligible targets must be
active standard concepts in the input domain. Optional `concept_class_ids` and
`vocabulary_ids` constrain targets before nearest-common selection. Ranking uses
the maximum stored minimum separation across inputs; distance ties abstain.
`common_ancestors` exposes ranked alternatives on successful and tied results.
Backend failures and missing ancestor metadata raise errors rather than becoming
no-match results. Concept enrichment elsewhere retains its tolerant default.

### Upstream path

1. Move `q_ancestor_rows` and its raw-row contract to omop-graph, with tests for
   non-self zero distances, self policy, batch input, bounds and empty input.
2. Add an omop-graph runtime method that executes that query and returns the same
   four fields. It must preserve stored distances rather than counting BFS hops.
3. After releasing and pinning that dependency, replace the implementation of
   `OmopGraphAdapter.ancestor_rows()` with delegation to the upstream method.
   Remove the temporary module and import; no mapping-service or plugin API
   change is required. Keep `tests/unit/test_stored_ancestry.py` as the consumer
   contract and run it against the upstream implementation.
4. Audit existing upstream `q_concept_potential_ancestor*` helpers separately:
   their positive-distance filter also excludes non-self zero-separation rows.
   Do not change those callers' behavior implicitly during migration.

Drug-specific ingredient/form/strength checks and broad-match acceptance remain
in the PBS plugin. Groundworkers makes no global preference between Clinical Drug
and Branded Drug; callers can request classes explicitly.
