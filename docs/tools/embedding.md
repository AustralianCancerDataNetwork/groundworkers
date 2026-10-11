# Embedding Tools

Five tools expose the embedding index. They require `embedding_model_name` and `vector_store_name` in `[tools.groundworkers]`.

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

When the backend is unavailable or no models are registered, `"available"` is `false` and `"models"` is an empty array. This tool never returns an error dict — it degrades gracefully. A legacy registry, misplaced registry, or missing model table is reported in `detail` with a specific explanation.

Groundworkers opens the store through omop-emb's read-only reader for status and query tools. A pre-3.0 store reports `LEGACY_REGISTRY`. The migration script is tracked in oa-configurator issue #40; once it ships, Groundworkers will show omop-emb's migration command. Groundworkers does not migrate the store.

## `embedding_neighbours`

Returns the nearest embedding-space neighbours for one OMOP concept by looking up its existing vector in the index.

By default `standard_only` and `active_only` are both `true`, matching the partial HNSW index predicate. Set either to `false` to widen the result set.

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

!!! warning "Requires a configured embedding model"
    `embedding_search` must encode the query string using a live embedding model.
    Configure `embedding_model_name` in `[tools.groundworkers]` and give its
    `[models.*]` entry a provider in `[providers.*]`. Without a live model client,
    the tool returns `BACKEND_UNAVAIL`.

## `embedding_search_batch`

Encodes and searches multiple query strings in one provider/index operation. The results are aligned with the input order, so `results[i]` belongs to
`queries[i]`.

```json
{
  "queries": ["lung adenocarcinoma", "type 2 diabetes"],
  "limit": 5,
  "domain": "Condition",
  "standard_only": true,
  "active_only": true,
  "batch_size": 32
}
```

All queries in a call share the same domain, vocabulary, model, and concept filters. Partition queries before calling when those constraints differ. The MCP wrapper accepts at most 256 queries; `batch_size` is the provider batch size and is bounded to 1–128.

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

Like `embedding_search`, this requires a configured `embedding_model_name` and a live provider client.
