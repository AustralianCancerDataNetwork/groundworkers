# Tools Overview

groundworkers registers tools in six groups.  Tools are only registered when the relevant
adapter is present in the config — absent adapters simply mean those tools are not
available in that deployment.

| Group | Tools | Requires |
|---|---|---|
| **Concept** | `concept_get`, `concept_by_code`, `concept_ancestors`, `concept_descendants`, `concept_relationships`, `concept_equivalency_path`, `concept_path`, `concept_neighbors`, `concept_map_to_standard` | `omop_graph` |
| **Resolver** | `concept_ground` | `omop_graph` |
| **Search** | `concept_search_exact`, `concept_search_fulltext`, `concept_navigate_to_standard` | `omop_graph` |
| **Embedding** | `embedding_index_status`, `embedding_neighbours`, `embedding_search`, `embedding_encode` | `omop_emb` |
| **Cohort** | `cohort_find_concept_references` | `oa_cohorts` |
| **System** | `system_status`, `system_vocabulary_catalogue` | Always registered |

!!! info "system tools are always registered"
    `system_status` and `system_vocabulary_catalogue` are registered unconditionally so
    clients always get a structured response.  Absent adapters are reported as
    `"available": false` rather than causing an "unknown tool" error.

## Tool groups at a glance

**Concept tools** — deterministic lookups from known identifiers (concept_id, vocab+code,
hierarchy traversal, path finding).  Input → exact fact.

**Resolver tools** — free-text grounding via a tiered pipeline (Exact → FullText →
Embedding → Partial).  Input → ranked candidates.

**Search tools** — agent-composable primitives that expose raw quality signals
(ts_rank, standard_concept flag) for the caller to act on.  Use these when you need
finer control than `concept_ground` provides.

## Error response shape

Every tool returns a plain dict.  On failure the dict has:

```json
{"error": true, "code": "ERROR_CODE", "message": "Human-readable description"}
```

| Code | Meaning |
|---|---|
| `NOT_FOUND` | Requested concept ID or code does not exist |
| `INVALID_INPUT` | Bad argument (non-positive concept_id, empty string, etc.) |
| `BACKEND_UNAVAIL` | Adapter not configured, embedding index unavailable, or feature not yet implemented |
| `QUERY_ERROR` | Database or adapter error during the query |

## Input validation

All tools validate their inputs before hitting the adapter:

- `concept_id` must be a positive integer (`> 0`)
- String arguments (`vocabulary_id`, `concept_code`, `query`) must be non-empty after stripping
- `limit` arguments for search/embedding results are clamped to `[1, 50]`
- `max_depth` for `concept_ancestors` is clamped to `[1, 20]`; for `concept_descendants` to `[1, 10]`; for `concept_neighbors` to `[1, 4]`
- `max_nodes` for `concept_neighbors` is clamped to `[10, 500]`

Validation failures return `INVALID_INPUT` without touching the database.

## Inspecting registered tools

```bash
groundworkers --config /path/to/config.yaml --describe
```

This prints a JSON object with every registered tool's signature and docstring —
useful for verifying which tools are active in a given deployment.
