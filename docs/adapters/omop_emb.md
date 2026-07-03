# OmopEmb Adapter

`OmopEmbAdapter` wraps [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/)
for embedding-backed concept retrieval.

## What it owns

The adapter is responsible for:

- nearest-neighbour lookup against the configured embedding store
- optional on-the-fly query encoding through an embedding client
- exposing backend availability and registered model metadata

The adapter does **not** resolve stack config itself. `build_application(...)`
constructs it from the already-resolved `omop-emb` package config and any
required engines.

## Backends

`groundworkers` supports the same primary storage backends it wires from
`omop-emb`:

| Backend | omop-emb config |
|---|---|
| `sqlitevec` | `backend = "sqlitevec"` plus `sqlite_path` |
| `pgvector` | `backend = "pgvector"` plus a configured embedding resource |

FAISS remains a sidecar acceleration layer rather than a standalone primary
backend.

## Operation modes

### Index lookup mode

No live model API is required.

- `embedding_neighbours` reads an existing concept vector from the index
- `index_status` reports registered models and concept counts

### Text search mode

Requires an embedding client configured through `omop-emb` package settings.

- `embedding_search` encodes a text query on the fly
- `embedding_encode` returns a raw embedding vector

## Availability model

The underlying backend is built lazily on first use. If the backend cannot be
opened, the adapter reports unavailability rather than failing application
construction for callers that do not need embedding-backed operations.

Representative `index_status()` shape:

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

## Where it is used

- embedding MCP tools
- the embedding channel inside `MappingService.concept_candidate_bundle(...)`
- optional embedding-tier support inside graph grounding when an embedding client
  is successfully wired into `OmopGraphAdapter`
