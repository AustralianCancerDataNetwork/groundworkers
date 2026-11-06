# OmopEmb Adapter

`OmopEmbAdapter` wraps [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/) for embedding-backed concept retrieval.

## What it owns

The adapter is responsible for:

- nearest-neighbour lookup against the configured embedding store
- optional on-the-fly query encoding through an embedding client
- exposing backend availability and registered model metadata

The adapter does **not** resolve stack config itself. `build_application(...)` constructs it from Groundworkers' `[tools.groundworkers]` `embedding_model_name` and `vector_store_name` references.

## Backends

`groundworkers` supports the same primary storage backends it wires from `omop-emb`:

| Backend | Groundworkers stack config |
|---|---|
| `sqlitevec` | a `[vector_stores.*]` entry with `backend_type = "sqlitevec"` and a database reference |
| `pgvector` | a `[vector_stores.*]` entry with `backend_type = "pgvector"` and a `database` reference |
| `duckdbvss` | a `[vector_stores.*]` entry with `backend_type = "duckdbvss"` and a separate prebuilt DuckDB database reference |

FAISS remains a sidecar acceleration layer rather than a standalone primary backend.

See [database and vector-storage configuration](../usage/configuration.md#databases-and-vector-storage)
for connection dialects and file-serving requirements.

## Operation modes

### Index lookup mode

No live model API is required.

- `embedding_neighbours` reads an existing concept vector from the index
- `index_status` reports registered models and concept counts

### Text search mode

Requires `embedding_model_name` and `vector_store_name`, plus a configured embedding provider/model.

- `embedding_search` encodes a text query on the fly
- `embedding_search_batch` encodes and searches multiple text queries with one provider/index batch, returning rows aligned to input order
- `embedding_encode` returns a raw embedding vector

## Availability model

Configuration is validated when constructing the application. A configured
DuckDB embedding file must already exist and be separate from the CDM file;
invalid file configuration stops startup.

The underlying backend is built lazily on first use. Failures while opening a
validly configured backend are reported as embedding unavailability. Registered
models and stored vectors can be inspected without a live query encoder.

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
