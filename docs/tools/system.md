# System Tools

Two tools report on overall availability and the OMOP vocabulary catalogue for a `groundworkers` deployment. Both are always registered.

## `system_status`

Returns availability and health details for each configured backend. Takes no arguments.

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
      "backend_type": "pgvector",
      "model_count": 1,
      "model_backend_configured": true,
      "detail": null
    }
  }
}
```

`overall` is one of:

- `healthy`: all configured components are available
- `degraded`: at least one configured component is unavailable
- `unavailable`: no configured components are available, or none are configured

`components` only includes backends that are configured for the current runtime. For `omop_emb`, `available` means the vector store can be inspected and contains at least one registered model. `model_backend_configured` tells you whether live text search and encoding can call the configured embedding model. Stored-neighbour lookup can still work when this is `false`.

Use this tool when you want to confirm:

- whether the omop-graph backend is available
- whether embedding search is available
- whether the embedding tier of `concept_ground` is active

## `system_vocabulary_catalogue`

Returns vocabularies, domains, and concept classes from the OMOP vocabulary database.

This requires the omop-graph backend to be configured and returns `BACKEND_UNAVAIL` otherwise.

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

Clients often cache this response so they can present vocabulary and domain choices without re-querying the server repeatedly.
