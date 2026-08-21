# Embedding Tools

Four tools expose the embedding index. All require `omop_emb` to be enabled in the config.

## `embedding_index_status`

Returns the status of the embedding backend and all registered models. Takes no arguments.

**Response**:
```json
{
  "available": true,
  "backend_type": "pgvector",
  "models": [
    {
      "model_name": "qwen3-embedding:0.6b",
      "provider": "OLLAMA",
      "dimensions": 1024,
      "index_type": "FLAT",
      "concept_count": 438924
    }
  ]
}
```

When the backend is unavailable or no models are registered, `"available"` is `false` and `"models"` is an empty array. This tool never returns an error dict — it degrades gracefully.

## `embedding_neighbours`

Returns the nearest embedding-space neighbours for one OMOP concept by looking up its existing vector in the index.

```json
{"concept_id": 4119419, "limit": 10, "model_name": "qwen3-embedding:0.6b"}
```

`model_name` is optional; if omitted the default model is used. `limit` is clamped to `[1, 50]`.

**Response**:
```json
{
  "query_concept_id": 4119419,
  "model_name": "qwen3-embedding:0.6b",
  "results": [
    {"concept_id": ..., "concept_name": "...", "similarity": 0.943, "is_standard": true, "is_active": true}
  ]
}
```

Returns `NOT_FOUND` if the concept is not present in the embedding index.

## `embedding_search`

Encodes a text query on the fly and returns the nearest concepts by vector similarity.

```json
{
  "query": "lung adenocarcinoma",
  "limit": 10,
  "domain": "Condition",
  "vocabulary": "SNOMED",
  "standard_only": true,
  "active_only": true,
  "model_name": null
}
```

All parameters except `query` are optional.

!!! warning "Requires `api_base` configuration"
    `embedding_search` must encode the query string using a live embedding model.
    This requires `omop_emb.api_base` (and `api_key`) to be configured.  Without it
    the tool returns `BACKEND_UNAVAIL`.

## `embedding_encode`

Encodes a text string into one query embedding vector.

```json
{
  "text": "lung adenocarcinoma",
  "model_name": null
}
```

**Response**:
```json
{
  "text": "lung adenocarcinoma",
  "model_name": "qwen3-embedding:0.6b",
  "dimensions": 1024,
  "vector": [0.0123, -0.0441, ...]
}
```

Like `embedding_search`, this requires `omop_emb.api_base` and `omop_emb.api_key` to be configured.
