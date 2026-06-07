# OmopEmb Adapter

`OmopEmbAdapter` wraps [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/)
to provide embedding index queries.  It supports two storage backends and operates in
one of two modes depending on whether an encoding client is configured.

## Storage backends

| Backend | Config | Description |
|---|---|---|
| `sqlitevec` | `db_path: /path/to/index.db` | File-based; zero external dependencies |
| `pgvector` | `db_url: postgresql://...` | PostgreSQL; scales to larger indexes |

FAISS can be layered on top of either backend via `faiss_cache_dir` to accelerate
nearest-neighbour queries. FAISS is a read-time sidecar cache — it is not a standalone
backend and requires `sqlitevec` or `pgvector` to be configured alongside it.

## Operation modes

### Index lookup mode (no encoding client required)

`embedding_neighbours` fetches the stored vector for a concept ID from the index and
returns the nearest neighbours by similarity. No API key is needed — all data is in
the index.

### Text search mode (encoding client required)

`embedding_search` and `embedding_encode` encode text on the fly using a live embedding
model (`api_base` + `api_key`).

- `embedding_search`: encodes a query string and returns the nearest matching concepts
  with optional domain, vocabulary, standard, and active-status filtering.
- `embedding_encode`: returns the raw embedding vector for a text string.

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
