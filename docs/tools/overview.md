# Tools overview

The MCP surface is organized by the job a caller is trying to do. Tool registration follows the active runtime: core vocabulary and graph tools require the CDM runtime, while embedding, chat, knowledge-pack, and semantic-projection tools appear only when their prerequisites are available.

## Choose a workflow

| Need | Start with | What it gives you |
|---|---|---|
| Inspect a known concept or relationship | Concept tools | Deterministic concept records, hierarchy, paths, edges, and standard mappings |
| Search with a specific retrieval signal | Search tools or embedding tools | Candidate records plus the signal used to retrieve them |
| Resolve free text with a ranked policy | `concept_ground` | First successful resolver tier, ranked results, and grounding explanation |
| Preserve evidence for mapping review | Mapping tools | A candidate union, standard targets, hierarchy, relationships, and evaluation helpers |
| Prepare noisy clinical text | Text tools | Normalized, decomposed, cleaned, or disambiguated search phrases |
| Classify structured fields | `domain_classify` | Best-effort OMOP domain hints for later retrieval |
| Analyze a source artifact before ingest | Source planning tools | Neutral tables, column roles, route decisions, warnings, and provenance |
| Find reusable operational guidance | Knowledge tools | Applicable knowledge-pack manifests and pack content |
| Turn a grounded concept into CDM rows | `semantic_project` | Deterministic output rows, links, unresolved fields, or suppression reasons |
| Check what this deployment can do | System tools and `--describe` | Backend availability, vocabulary catalogue, and registered MCP surfaces |

## Tool groups

| Group | Tools | Availability |
|---|---|---|
| Concept | `concept_get`, `concept_by_code`, `concept_ancestors`, `concept_descendants`, `concept_relationships`, `concept_equivalency_path`, `concept_path`, `concept_neighbors`, `concept_associations`, `concept_extended_inheritance`, `concept_map_to_standard` | CDM and graph runtime |
| Resolver | `concept_ground` | Graph runtime |
| Search | `concept_search_exact`, `concept_search_fulltext`, `concept_navigate_to_standard` | CDM vocabulary runtime |
| Mapping | `concept_search_normalized`, `concept_candidate_bundle`, `concept_nearest_standard_ancestor`, `concept_mapping_context`, `concept_map_to_value`, `concept_resolve_mapping_expression`, `mapping_evaluate_candidates` | CDM runtime; some operations also require graph or embeddings |
| Embedding | `embedding_index_status`, `embedding_neighbours`, `embedding_search`, `embedding_encode` | `omop_emb` configured |
| Text | `text_normalize`, `text_mapping_cleanup`, `text_decompose`, `text_disambiguate` | Chat model configured |
| Domain | `domain_classify` | Chat model configured |
| Source planning | `source_plan`, `source_plan_assisted` | Source-planning service; assisted path also needs chat |
| Knowledge | `knowledge_catalogue`, `knowledge_pack` | Bundled or configured packs available |
| System | `system_status`, `system_vocabulary_catalogue` | Always registered |
| Semantic projection | `semantic_project` | `semantic_projection_enabled = true` |

The source-planning resources `source-planning://canonical-headers`, `source-planning://column-roles`, and `source-planning://ingestion-strategies` are also registered independently of the tool table. Text preprocessing prompts are available through MCP prompt discovery when the server starts.

## MCP, REST, and Python

MCP is the broad, discovery-oriented surface. REST intentionally exposes only curated workflow operations: candidate bundles and assisted source planning, plus `/healthz`. Direct Python callers should use `app.services.*` for domain workflows and should not import MCP tool modules to avoid a transport-shaped API.

The one service that is not attached to `app.services` is `SemanticProjectionService`: it has no adapter dependency and is constructed directly when the feature is enabled. The [integration guide](../usage/integrations.md) shows the three entry points; the [concepts guide](../concepts.md) explains when each one is appropriate.

## Availability and errors

`system_status` and `system_vocabulary_catalogue` are always registered so a client can inspect the deployment before assuming optional capabilities exist. Other tool groups are omitted when their backing runtime is not configured. Use `groundworkers --describe` to inspect the exact active surface.

MCP tools return a JSON-safe error object rather than raising across the transport boundary:

```json
{"error": true, "code": "ERROR_CODE", "message": "Human-readable description"}
```

Common codes are `NOT_FOUND`, `INVALID_INPUT`, `BACKEND_UNAVAIL`, `QUERY_ERROR`, and `INTERNAL_ERROR`. Direct Python services raise `GroundworkersError` or `ValueError` instead; REST maps the same categories to HTTP responses.
