# Configuration

groundworkers uses the same `AppConfig` model for both supported integration styles:

- MCP server mode via `groundworkers --config ...`
- direct Python mode via `AppConfig.load(...)` and `build_application(config)`

There is no separate "server config" and "library config". The same object graph is
reused by both.

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

## With LLM text tools

```yaml
omop_graph:
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  vocab_schema: omop_vocab

llm:
  enabled: true
  provider: openai-compatible
  api_base: "http://localhost:11434/v1"
  api_key: "ollama"
  default_model_name: qwen3:8b
```

## Configuration reference

### `app_name`

| Field | Type | Default | Description |
|---|---|---|---|
| `app_name` | `str` | `groundworkers` | MCP server name and application identifier used by `GroundcrewServer`. |

### `omop_graph`

Connects to the OMOP vocabulary database via omop-graph.

| Field | Type | Default | Description |
|---|---|---|---|
| `db_url` | `str` | — | **Required.** SQLAlchemy connection URL for the OMOP CDM database. |
| `vocab_schema` | `str` | `omop_vocab` | Schema containing OMOP vocabulary tables. Only letters, digits, and underscores allowed. |
| `emb_model_name` | `str` | `null` | Default embedding model name used by the `EMBEDDING_NEAREST` grounding tier in `concept_ground`. |
| `min_fulltext_overlap` | `float` | `0.0` | Minimum proportion of query tokens that must overlap a full-text hit before `concept_ground` accepts it. |

When `omop_graph` is configured:

- `CDMAdapter` and `OmopGraphAdapter` are built
- `VocabService` is built against the same CDM connection
- `MappingService` is built on top of `VocabService` and `OmopGraphAdapter`
- concept, resolver, search, mapping, and system tools become available
- `MappingService` becomes available via `app.services.mapping`
- `VocabService` becomes available via `app.services.vocab`

!!! note "Full-text search is auto-detected"
    The `concept_ground` and `concept_search_fulltext` tools use PostgreSQL tsvector
    sidecar columns (`concept_name_tsvector`, `concept_synonym_name_tsvector`) when
    they are present. No configuration is required; the adapter inspects the schema
    on first use.

### `omop_emb`

Configures the embedding index adapter.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Must be `true` for embedding tools to be registered. |
| `backend_type` | `sqlitevec` \| `pgvector` | `sqlitevec` | Embedding storage backend. `sqlitevec` and `pgvector` are the only supported primary backends. |
| `db_path` | `str` | `null` | Path to the sqlite-vec database file (required for `sqlitevec`). |
| `db_url` | `str` | `null` | SQLAlchemy URL (required for `pgvector`). |
| `default_model_name` | `str` | `null` | Model to use when no `model_name` argument is supplied by the caller. |
| `faiss_cache_dir` | `str` | `null` | Directory for a FAISS sidecar cache. FAISS accelerates nearest-neighbour queries but requires a primary backend (`sqlitevec` or `pgvector`) to be configured alongside it. |
| `api_base` | `str` | `null` | Embedding API base URL for on-the-fly query encoding. |
| `api_key` | `str` | `null` | API key for the embedding service when `api_base` is set. |

!!! note "`embedding_search` requires `api_base`"
    `embedding_search` encodes the query string on the fly. This requires a configured
    `api_base` and `api_key`. `embedding_neighbours` does not encode any text; it
    looks up an existing concept embedding by ID and does not need `api_base`.

When `omop_emb.enabled` is true:

- embedding MCP tools are registered
- `MappingService` can optionally use the embedding channel for candidate bundles
- `concept_mapping_context` can optionally add embedding neighbors

### `llm`

Configures the LLM adapter for LLM-backed text preprocessing and domain
classification tools.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Must be `true` for text and domain tools to be registered. |
| `provider` | `str` | `openai-compatible` | API provider label. Use `"openai-compatible"` for OpenAI-compatible endpoints (including Ollama). |
| `api_base` | `str` | `null` | Base URL for the model API. |
| `api_key` | `str` | `null` | API key. Use `"ollama"` as a placeholder for Ollama (which does not require authentication). |
| `default_model_name` | `str` | `null` | Model to use when no `model_name` argument is supplied by the caller. |

When `llm.enabled` is true:

- `LLMAdapter` is built and both `TextService` and `DomainService` are wired on top of it
- `text_normalize`, `text_decompose`, and `text_disambiguate` MCP tools are registered
- `domain_classify` MCP tool is registered
- `TextService` becomes available via `app.services.text`
- `DomainService` becomes available via `app.services.domain`
- All vocabulary, search, mapping, and embedding tools remain fully functional
  regardless of whether `llm` is configured

## Direct Python example

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.load("config/groundworkers.local.yaml")
app = build_application(config)
mapping = app.services.mapping
```

The service availability follows the same configuration as the MCP server. If the
required adapters are absent, the corresponding service attributes are `None`.
