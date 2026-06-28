# System Tools

Two tools report on overall availability and the OMOP vocabulary catalogue for a
`groundworkers` deployment. Both are always registered.

## `system_status`

Returns the availability of each configured adapter. Takes no arguments.

**Response**:

```json
{
  "overall": "healthy",
  "components": {
    "omop_graph": {
      "available": true,
      "db_connected": true,
      "embedding_resolver_active": true,
      "detail": null
    },
    "omop_emb": {
      "available": true,
      "backend_type": "sqlitevec",
      "model_count": 1,
      "client_configured": true,
      "detail": null
    },
    "llm": {
      "available": true,
      "provider": "openai-compatible",
      "default_model": "qwen3:latest",
      "structured_output_supported": true,
      "detail": null
    }
  }
}
```

`overall` summarises the deployment state:

| Value | Meaning |
|---|---|
| `"healthy"` | All configured components are available |
| `"degraded"` | At least one configured component is unavailable |
| `"unavailable"` | No components are configured or none are reachable |

`components` only contains entries for adapters that are configured in the active
config — unconfigured adapters are omitted entirely.

Notable fields per component:

- `omop_graph.embedding_resolver_active` — `true` only when an `EmbeddingClient`
  was successfully wired into the graph adapter at startup.  This is independent
  from `omop_emb.available` and must be checked separately to confirm the embedding
  tier of `concept_ground` is operational.
- `omop_emb.client_configured` — `true` when an API client was provided for
  on-the-fly embedding; embedding search still works without a client if the
  index already contains stored vectors.
- `llm.structured_output_supported` — reflects whether the configured provider
  supports structured output mode.

Use this tool when you want to confirm:

- whether vocabulary lookup is available
- whether embedding search is available
- whether the LLM backend is reachable
- whether the embedding tier of `concept_ground` is active (`embedding_resolver_active`)

## `system_vocabulary_catalogue`

Returns vocabularies, domains, and concept classes from the OMOP vocabulary
database.

This requires `omop_graph` to be configured and returns `BACKEND_UNAVAIL`
otherwise.

**Response**:

```json
{
  "vocabularies": [
    {"vocabulary_id": "SNOMED", "vocabulary_name": "SNOMED CT", "concept_count": 423891}
  ],
  "domains": [
    {"domain_id": "Condition", "domain_name": "Condition", "concept_count": 153240}
  ],
  "concept_classes": [
    {"concept_class_id": "Clinical Finding", "concept_class_name": "Clinical Finding"}
  ]
}
```

Clients often cache this response so they can present vocabulary and domain choices
without re-querying the server repeatedly.
