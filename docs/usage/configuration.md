# Configuration

`groundworkers` uses the shared OMOP stack configuration managed by `oa-configurator`. `build_app_config(...)` resolves that shared stack into a runtime `AppConfig`; callers do not provide a separate `groundworkers`-specific runtime file.

## Runtime entrypoints

Use these helpers depending on how much control you need:

```python
from groundworkers.bootstrap import build_app_config, build_app_config_from_stack
```

- `build_app_config()` loads the active shared stack config from disk
- `build_app_config(config_path=..., profile=...)` overrides the file or profile
- `build_app_config_from_stack(stack)` is useful in tests and programmatic tooling

Then construct the application container:

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)
```

The CLI entry point also accepts transport overrides and a flag for the interactive setup console:

```bash
groundworkers --transport streamable-http --host 127.0.0.1 --port 8000
groundworkers --tui
```

`--tui` launches the setup console and exits instead of starting an MCP or REST server. Install the optional TUI dependencies with `uv sync --extra tui` or `pip install groundworkers[tui]`.

## Configuration ownership

`groundworkers` does not own every setting it consumes. The shared stack keeps package ownership explicit:

| Concern | Where it lives |
|---|---|
| Physical connection credentials and endpoints | `[connections.*]` |
| Logical databases, schemas, and CDM/vocabulary roles | `[databases.*]` |
| Model provider endpoints and credentials | `[providers.*]` |
| Model identity and capabilities | `[models.*]` |
| Vector-store backend and its database | `[vector_stores.*]` |
| Which of those entries Groundworkers uses, plus MCP/REST defaults, grounding policy, source-planning, knowledge-pack, and semantic-projection settings | `[tools.groundworkers]` |

Groundworkers references stack entries **by name** and resolves them through `oa-configurator`. It does not read any other package's configuration section.

## Typical TOML shape

This is the steady-state structure to expect in `config.toml`:

```toml
[connections.cdm_main]
dialect = "postgresql+psycopg"
host = "localhost"
port = 5432
user = "omop"
password = "…"
database_name = "omop"

[databases.cdm_db]
kind = "cdm"
connection = "cdm_main"
schema_name = "omop"
vocab_schema = "omop_vocab"

[providers.local_ollama]
provider = "ollama"
base_url = "http://localhost:11434"

[models.embedding_model]
provider = "local_ollama"
model = "qwen3-embedding:0.6b"
embedding_dim = 1024
embeddings = true

[models.chat_model]
provider = "local_ollama"
model = "qwen3:8b"
structured_output = true

[vector_stores.embeddings]
backend_type = "pgvector"
database = "vector_db"

[tools.groundworkers]
cdm_db = "cdm_db"
embedding_model_name = "embedding_model"
llm_model_name = "chat_model"
vector_store_name = "embeddings"
app_name = "groundworkers"

mcp_transport = "streamable-http"
mcp_host = "127.0.0.1"
mcp_port = 8000

rest_enabled = true
rest_host = "127.0.0.1"
rest_port = 8080
rest_base_path = "/v1"

grounding_min_fulltext_overlap = 0.5
grounding_max_depth = 5

source_planning_llm_assisted_enabled = true
```

Settings are flat, grouped by name prefix - e.g. `omop-config configure groundworkers --grounding-max-depth 6` updates that one field and leaves its neighbours untouched. 

A CDM-only stack is valid: omit `embedding_model_name` and `vector_store_name` and every lexical feature still works. See [Runtime combinations](#what-becomes-available-at-runtime).

### Reference fields

| Field | References | Required |
|---|---|---|
| `cdm_db` | a `[databases.*]` entry with `kind = "cdm"` | yes |
| `embedding_model_name` | a `[models.*]` entry | no |
| `llm_model_name` | a `[models.*]` entry | no |
| `vector_store_name` | a `[vector_stores.*]` entry | no |

`embedding_model_name` is the **embedding** model and `llm_model_name` is the **chat** model. Both name `[models.*]` entries and the two are never interchanged; they may point at different providers, or one may be unset. There is no separate on/off flag: chat is available exactly when `llm_model_name` resolves.

## groundworkers-owned fields

### `app_name`

Application identity used by:

- MCP server naming
- REST application title
- runtime describe output

Default: `groundworkers`

### MCP transport (`mcp_*`)

Default MCP startup settings used when you do not override them on the CLI.

| Field | Type | Default |
|---|---|---|
| `mcp_transport` | `stdio` \| `sse` \| `streamable-http` | `stdio` |
| `mcp_host` | `str` | `127.0.0.1` |
| `mcp_port` | `int` | `8000` |

### REST transport (`rest_*`)

Default REST startup settings.

| Field | Type | Default |
|---|---|---|
| `rest_enabled` | `bool` | `false` |
| `rest_host` | `str` | `127.0.0.1` |
| `rest_port` | `int` | `8080` |
| `rest_base_path` | `str` | `/v1` |

`base_path` is validated to begin with `/`.

### `llm_model_name` and chat

The chat model used by `TextService`, `DomainService`, and LLM-assisted source planning is a named `[models.*]` entry, exactly like the embedding model. Its endpoint and credentials live on the `[providers.*]` entry that model references, so they are stored, redacted, and reused the same way as every other provider in the stack:

```toml
[providers.local_ollama]
provider = "ollama"
base_url = "http://localhost:11434/v1"
api_key = "…"

[models.chat_model]
provider = "local_ollama"
model = "qwen3:8b"
structured_output = true

[tools.groundworkers]
llm_model_name = "chat_model"
```

`structured_output = true` declares that the model can honour the JSON-mode request `complete_structured` makes; omop-llm treats every capability as opt-in. Chat is configured through the setup console's Chat section, which writes the provider and model entries through the same configuration provider as every other setup journey.

### Grounding (`grounding_*`)

Groundworkers-owned grounding policy. omop-graph's traversal limits are per-call arguments, not shared configuration, so they do not appear here.

| Field | Type | Default |
|---|---|---|
| `grounding_min_fulltext_overlap` | `float` | `0.0` |
| `grounding_max_depth` | `int` (1-10) | `5` |

`min_fulltext_overlap` must be between `0.0` and `1.0`. It is the minimum proportion of query tokens that must appear in a matched concept name for a full-text hit to be accepted; below the threshold grounding falls through to the next tier.

`max_depth` bounds the hierarchy distance between a grounding candidate and a required parent concept, or the identity-hop count when grounding runs without `parent_ids`.

### Concept flag contract

Grounding results carry strict OMOP flags:

| Field | Meaning |
|---|---|
| `standard_concept` | true only for raw `standard_concept = 'S'` |
| `classification_concept` | true only for raw `standard_concept = 'C'` |
| `is_active` | `invalid_reason` unset, treating blank and whitespace-only as active |

Grounding can legitimately land on a classification concept (an ATC or CPT4 hierarchy node). Those are valid hierarchy positions but **not** valid mapping targets for a CDM entity field, so check the flags rather than assuming every result is standard.

`concept_ground` also accepts `standard_only` and `active_only` (both default `false`). They narrow *candidate resolution*, not the returned results.

### Source planning (`source_planning_*`)

| Field | Type | Default |
|---|---|---|
| `source_planning_llm_assisted_enabled` | `bool` | `true` |

This controls whether `build_application(...)` wires the assisted classifier into `SourcePlanningService` when an LLM adapter is present.

### Knowledge packs (`knowledge_*`)

| Field | Type | Default |
|---|---|---|
| `knowledge_packs_root` | `str \| null` | `null` |

`groundworkers` includes bundled baseline knowledge packs as part of the package. Set `knowledge_packs_root` when you want to add site-specific or localisation packs on top of that baseline.

The configured directory should contain a `packs/` tree grouped by knowledge layer, for example:

```text
my-knowledge/
  packs/
    localisation/
      nacc-uds-v4/
        manifest.yaml
        guidance.md
```

If a configured pack has the same `layer` and `name` as a bundled baseline pack, the configured copy wins.

### Semantic projection (`semantic_projection_*`)

| Field | Type | Default |
|---|---|---|
| `semantic_projection_enabled` | `bool` | `false` |

Gates the `semantic_project` MCP tool and its backing `SemanticProjectionService`.

```toml
[tools.groundworkers]
semantic_projection_enabled = true
```

## What becomes available at runtime

### With `cdm_db` alone

Graph availability follows the resolved CDM database, so a CDM-only stack gets:

- `CDMAdapter`
- `OmopGraphAdapter` (lexical only)
- `VocabService`, `GraphService`, `ConceptGroundingService`, `MappingService`
- concept, resolver, search, mapping, source-planning, knowledge, and system MCP tools

Embedding status reports as unconfigured. No `[tools.omop_graph]` section is required or read.

### With `embedding_model_name` **and** `vector_store_name`

You additionally get:

- `OmopEmbAdapter`
- embedding MCP tools
- embedding-backed channels in `MappingService`
- the embedding grounding tier

Both references are required. With only one configured, status reports an actionable incomplete-configuration message and embedding features stay off rather than guessing a default.

The store must be populated before the embedding tier can return anything; population is an explicit operator action (see the Embeddings setup section) and never starts implicitly at query time. The server holds the graph read-only (`write=False`), so it never writes vectors — Groundworkers encodes the query text itself for the embedding tier.

### With `groundworkers.llm_model_name` configured

You additionally get:

- `LLMAdapter`
- `TextService`
- `DomainService`
- text and domain MCP tools
- LLM-assisted source planning when `source_planning.llm_assisted_enabled = true`

### With `semantic_projection_enabled = true`

You additionally get:

- `SemanticProjectionService` (constructed directly, not part of `app.services` — it needs no adapter)
- the `semantic_project` MCP tool

## CLI selection rules

By default:

- `groundworkers` loads the stack config file
- MCP startup uses the `mcp` defaults unless overridden on the CLI

Available runtime selectors:

```bash
groundworkers --config-path /path/to/config.toml --describe
groundworkers --transport streamable-http --host 0.0.0.0 --port 8000
groundworkers --transport rest --host 0.0.0.0 --port 8080
```

Equivalent environment override:

- `OA_CONFIG_PATH`

There is no profile selection. Profiles, resource aliases, and `[resources.*]` bundles were removed with the 1.0 stack; keep separate config files and select them with `--config-path` or `OA_CONFIG_PATH` instead.

## Direct Python example

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config(config_path="/path/to/config.toml")
app = build_application(config)

grounding = app.services.grounding
mapping = app.services.mapping
```

Service attributes are `None` only when their prerequisites are not available in the resolved runtime.

`config.describe()` returns a redacted summary keyed by `database`, `model`, and `vector_store`. Passwords and API keys are masked; safe URLs never carry credentials.
