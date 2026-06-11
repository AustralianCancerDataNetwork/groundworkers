# Tools Overview

groundworkers registers eight tool groups. Tools appear only when the relevant
adapters or services are available in the config.

| Group | Tools | Requires |
|---|---|---|
| **Concept** | `concept_get`, `concept_by_code`, `concept_ancestors`, `concept_descendants`, `concept_relationships`, `concept_equivalency_path`, `concept_path`, `concept_neighbors`, `concept_map_to_standard` | `omop_graph` |
| **Resolver** | `concept_ground` | `omop_graph` |
| **Search** | `concept_search_exact`, `concept_search_fulltext`, `concept_navigate_to_standard` | `omop_graph` |
| **Mapping** | `concept_search_normalized`, `concept_candidate_bundle`, `concept_nearest_standard_ancestor`, `concept_mapping_context`, `concept_map_to_value`, `concept_resolve_mapping_expression`, `mapping_evaluate_candidates` | `omop_graph` |
| **Embedding** | `embedding_index_status`, `embedding_neighbours`, `embedding_search`, `embedding_encode` | `omop_emb` |
| **Text** | `text_normalize`, `text_decompose`, `text_disambiguate` | `llm` |
| **Domain** | `domain_classify` | `llm` |
| **System** | `system_status`, `system_vocabulary_catalogue` | Always registered |

!!! info "System tools are always registered"
    `system_status` and `system_vocabulary_catalogue` are always present so clients can
    check what is available in the current deployment.

!!! info "Text tools require LLM configuration"
    `text_normalize`, `text_decompose`, and `text_disambiguate` are registered only
    when `llm` is configured in `AppConfig`. They are absent from `--describe` output
    when no LLM is configured.

!!! info "Domain tools require LLM configuration"
    `domain_classify` is registered only when `llm` is configured in `AppConfig`.
    It is intended for structured field sets such as data-dictionary attributes,
    not for free-text concept grounding.

## Tool groups at a glance

**Concept tools** return deterministic OMOP facts from known identifiers.

**Resolver tools** take free text and try to return the best grounded answer.

**Search tools** expose lexical retrieval with raw quality signals. Backed by
`VocabService`.

**Mapping tools** are aimed at review and adjudication workflows where the caller
often wants multiple evidence channels side by side. Backed by `MappingService`.

**Embedding tools** expose the embedding index directly.

**Text tools** preprocess free text using an LLM before grounding. Use these when
the input contains abbreviations, lay language, or ambiguous clinical phrases.
Backed by `TextService`.

**Domain tools** classify structured field labels into OMOP domains in one batch.
Use them when a caller already has field labels and example values and wants a
best-effort domain hint before downstream mapping. Backed by `DomainService`.

## Services and direct Python use

The mapping, search, text, and domain groups are also available directly through
`app.services.*`.

```mermaid
flowchart LR
    MCP[MCP caller] --> MT[tool]
    PY[Python caller] --> SVC[service]
    MT --> SVC
    SVC[VocabService\nMappingService\nTextService\nDomainService] --> ADP[CDMAdapter\nOmopGraphAdapter\nOmopEmbAdapter\nLLMAdapter]
```

If you are building a Python application, call `app.services.*` directly rather
than importing tool modules.

## Error response shape

Every tool returns a plain dict. On failure the dict has:

```json
{"error": true, "code": "ERROR_CODE", "message": "Human-readable description"}
```

| Code | Meaning |
|---|---|
| `NOT_FOUND` | Requested concept ID or code does not exist |
| `INVALID_INPUT` | Bad argument such as a non-positive `concept_id` or empty string |
| `BACKEND_UNAVAIL` | Required adapter is not configured or a backend is unavailable |
| `QUERY_ERROR` | Database or adapter error during the query |

## Input validation

All tools validate their inputs before hitting the adapter:

- `concept_id` must be a positive integer (`> 0`)
- string arguments such as `vocabulary_id`, `concept_code`, and `query` must be non-empty after stripping
- `limit` arguments for search and embedding results are clamped to `[1, 50]`
- `max_depth` for `concept_ancestors` is clamped to `[1, 20]`
- `max_depth` for `concept_descendants` is clamped to `[1, 10]`
- `max_depth` for `concept_neighbors` is clamped to `[1, 4]`
- `max_nodes` for `concept_neighbors` is clamped to `[10, 500]`
- mapping bundle `per_channel_limit` is clamped to `[1, 20]`
- mapping bundle `overall_limit` is clamped to `[1, 100]`

Validation failures return `INVALID_INPUT` without touching the database.

## Inspecting registered tools

```bash
groundworkers --config /path/to/config.yaml --describe
```

This prints a JSON object with each registered tool's signature and docstring, which
is useful when you want to confirm what is active in a particular deployment.
