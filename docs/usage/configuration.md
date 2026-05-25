# Configuration

groundworkers is configured with a YAML file.  Pass it at startup with `--config`.

## Minimal example (vocabulary only)

```yaml
omop_graph:
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  vocab_schema: omop_vocab
```

## With embedding search

```yaml
omop_graph:
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  vocab_schema: omop_vocab

omop_emb:
  enabled: true
  backend_type: pgvector
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  default_model_name: qwen3-embedding:0.6b
  api_base: "http://localhost:11434/v1"
  api_key: "ollama"
```

## With cohort database

```yaml
oa_cohorts:
  enabled: true
  db_url: "postgresql+psycopg://user:pass@localhost:5432/cohorts"
```

---

## Configuration reference

### `omop_graph`

Connects to the OMOP vocabulary database via omop-graph.

| Field | Type | Default | Description |
|---|---|---|---|
| `db_url` | `str` | — | **Required.** SQLAlchemy connection URL for the OMOP CDM database. |
| `vocab_schema` | `str` | `omop_vocab` | Schema containing OMOP vocabulary tables. Only letters, digits, and underscores allowed. |
| `emb_model_name` | `str` | `null` | Default embedding model name used by the `EMBEDDING_NEAREST` grounding tier in `concept_ground`. |

When `omop_graph` is absent, all concept, resolver, search, and system catalogue tools are not registered.

!!! note "Full-text search is auto-detected"
    The `concept_ground` and `concept_search_fulltext` tools use PostgreSQL tsvector
    sidecar columns (`concept_name_tsvector`, `concept_synonym_name_tsvector`) when
    they are present.  No configuration is required — the adapter detects them by
    inspecting the database schema on first use.

### `omop_emb`

Configures the embedding index adapter.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Must be `true` for embedding tools to be registered. |
| `backend_type` | `sqlitevec` \| `pgvector` | `sqlitevec` | Embedding storage backend. |
| `db_path` | `str` | `null` | Path to the sqlite-vec database file (required for `sqlitevec`). |
| `db_url` | `str` | `null` | SQLAlchemy URL (required for `pgvector`). |
| `default_model_name` | `str` | `null` | Model to use when no `model_name` argument is supplied by the caller. |
| `faiss_cache_dir` | `str` | `null` | Directory for FAISS index cache files. |
| `api_base` | `str` | `null` | Embedding API base URL (required for on-the-fly query encoding in `embedding_search`). |
| `api_key` | `str` | `null` | API key for the embedding service (required when `api_base` is set). |

!!! note "`embedding_search` requires `api_base`"
    `embedding_search` encodes the query string on the fly.  This requires a configured
    `api_base` and `api_key`.  `embedding_neighbours` does not encode any text — it
    looks up an existing concept embedding by ID and does not need `api_base`.

### `oa_cohorts`

Connects to an OpenAnalytics cohort database.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Must be `true` for cohort tools to be registered. |
| `db_url` | `str` | `null` | SQLAlchemy URL (required when `enabled = true`). |

!!! warning "Phase N"
    `oa_cohorts` support is not yet fully implemented.  The `cohort_find_concept_references`
    tool is registered and returns a `BACKEND_UNAVAIL` error until the backing query is
    complete.
