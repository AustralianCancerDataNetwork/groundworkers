# OmopEmb Adapter

`OmopEmbAdapter` wraps [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/)
to provide embedding index queries.  It supports two storage backends and two query
modes.

## Storage backends

| Backend | Config | Description |
|---|---|---|
| `sqlitevec` | `db_path: /path/to/index.db` | File-based; zero external dependencies |
| `pgvector` | `db_url: postgresql://...` | PostgreSQL; scales to larger indexes |

## Query modes

### Neighbour lookup (`embedding_neighbours`)

Fetches the existing vector for a concept ID from the index, then finds the nearest
neighbours by similarity.  Does **not** require an API key — all data is in the index.

### On-the-fly search (`embedding_search`)

Encodes the query string using a live embedding model (`api_base` + `api_key`), then
searches the index for similar concepts with optional domain, vocabulary, standard,
and active-status filtering.

### Raw encoding (`embedding_encode`)

Encodes one free-text string using the configured query embedding model and returns
the raw vector.  This is primarily intended as a building block for higher-level
ACP workflows.

## `index_status` response

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

## Lazy backend initialisation

The underlying backend object is created on first use (not at server startup).  If
the backend is unavailable, `index_status` returns `"available": false` with an empty
`models` list rather than crashing.
