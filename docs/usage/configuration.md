# Configuration

Groundworkers uses the shared OMOP stack configuration managed by `oa-configurator`. The file holds database connections, models, and other shared resources. The `[tools.groundworkers]` section selects which entries Groundworkers uses and sets its own defaults.

For a guided setup, start with [Initial local setup](../from-scratch.md). This page explains the file layout and individual settings.

## Runtime entrypoints

Open the setup console to create or edit a configuration:

```bash
groundworkers tui
```

Install the console with `pip install "groundworkers[tui]"`. The console edits configuration and runs checks; it does not start an MCP or REST service.

### Choose the configuration file

The CLI selects a file in this order:

1. `--config-path /path/to/config.toml`.
2. `OA_CONFIG_PATH` supplied by your shell or client environment.
3. `OA_CONFIG_PATH` saved in `~/.config/omop/groundworkers.env`.
4. The default, `~/.config/omop/config.toml`.

The console can remember a selected location in `groundworkers.env`; that file records the location of the TOML file, not a second set of runtime settings. An unusable environment path is reported so you can correct it. Use an explicit path when you need to be sure which configuration is selected:

```bash
groundworkers tui --config-path /path/to/config.toml
groundworkers --config-path /path/to/config.toml --describe
```

For a configuration managed by a deployment or copied from elsewhere, use:

```bash
groundworkers tui --config-path /path/to/config.toml --config-read-only
```

This disables configuration edits in the console. Database preparation and embedding population are separate operations that can still write to their target stores. Edit the authoritative configuration source, then reopen the console to check it. Restart a running service after changing its configuration.

## Configuration ownership

Shared resources have their own entries so several packages can use the same connection or model:

| Concern | Where it lives |
|---|---|
| Physical connection credentials and endpoints | `[connections.*]` |
| Logical databases, schemas, and CDM/vocabulary roles | `[databases.*]` |
| Model provider endpoints and credentials | `[providers.*]` |
| Model identity and capabilities | `[models.*]` |
| Vector-store backend and its database | `[vector_stores.*]` |
| Which of those entries Groundworkers uses, plus MCP/REST defaults, grounding policy, source-planning, knowledge-pack, and semantic-projection settings | `[tools.groundworkers]` |

Groundworkers references these entries **by name** and resolves them through `oa-configurator`. Its core runtime reads `[tools.groundworkers]` and the shared entries it names. Maintenance commands and installed plugins use their own package sections, as described below.

## Typical TOML shape

This example includes a CDM connection, PostgreSQL embeddings, and an Ollama chat model. Replace the connection details and choose models available from your provider. The model names and grounding threshold are examples, not required defaults.

```toml
[connections.cdm_main]
dialect = "postgresql+psycopg"
host = "localhost"
port = 5432
user = "omop"
password = "replace-me"
database_name = "omop"

[databases.cdm_db]
kind = "cdm"
connection = "cdm_main"
cdm_schema = "public"
vocab_schema = "public"

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

[databases.vector_db]
kind = "generic"
connection = "cdm_main"
schema_name = "groundworkers"

[tools.groundworkers]
cdm_db = "cdm_db"
embedding_model_name = "embedding_model"
llm_model_name = "chat_model"
vector_store_name = "embeddings"
app_name = "groundworkers"

mcp_transport = "streamable-http"
mcp_host = "127.0.0.1"
mcp_port = 8000

rest_host = "127.0.0.1"
rest_port = 8080
rest_base_path = "/v1"

grounding_min_fulltext_overlap = 0.5
grounding_max_depth = 5

source_planning_llm_assisted_enabled = true

[tools.omop_emb]
cdm_db = "cdm_db"
embedding_model_name = "embedding_model"
vector_store_name = "embeddings"

[tools.omop_alchemy]
cdm_db = "cdm_db"

[tools.omop_graph]
cdm_db = "cdm_db"
```

Settings are flat, grouped by name prefix. For example, `omop-config configure groundworkers --grounding-max-depth 6` updates that field without changing its neighbours.

For a CDM-only setup, keep the connection, CDM database, and `[tools.groundworkers]` entries. Omit the model and vector-store references and their unused definitions. Lexical operations remain available, although some searches need prepared indexes. See [Runtime combinations](#what-becomes-available-at-runtime).

The setup console also configures the companion commands it uses for maintenance:

| Package section | Used for |
|---|---|
| `[tools.omop_emb]` | Embedding population, using the same CDM, model, and vector-store references. Written when both embedding references are configured. |
| `[tools.omop_alchemy]` and `[tools.omop_graph]` | CDM schema, search-index, and graph preparation, using the same `cdm_db` reference. |

These sections point to the shared resources; they do not duplicate connection details or credentials. If you edit the file by hand, keep the references aligned before running maintenance. The core Groundworkers runtime does not take its graph or embedding settings from those companion sections.

### Reference fields

| Field | References | Required |
|---|---|---|
| `cdm_db` | a `[databases.*]` entry with `kind = "cdm"` | yes |
| `embedding_model_name` | a `[models.*]` entry | no |
| `llm_model_name` | a `[models.*]` entry | no |
| `vector_store_name` | a `[vector_stores.*]` entry | no |

`cdm_db` defaults to the name `cdm_db`, which must resolve to a CDM database entry. The other references are optional.

`embedding_model_name` selects the model for concept embeddings and live query encoding; `llm_model_name` selects the chat model. They can use different providers, and either can be left unset. They can share a model entry if it supports both capabilities. Chat tools are registered when `llm_model_name` resolves; use the console checks to verify that the provider and model actually work.

### A separate vocabulary connection

If the vocabulary tables live in another database, define its connection and set `vocab_connection` on the CDM database entry:

```toml
[connections.vocab_main]
dialect = "postgresql+psycopg"
host = "localhost"
port = 5432
user = "omop"
password = "replace-me"
database_name = "vocabulary"

[databases.cdm_db]
kind = "cdm"
connection = "cdm_main"
cdm_schema = "public"
vocab_connection = "vocab_main"
vocab_schema = "public"
```

Vocabulary reads use `vocab_connection` and `vocab_schema`. If you omit `vocab_connection`, they use the main CDM connection. Different schemas on the same connection need only separate `cdm_schema` and `vocab_schema` values.

## Groundworkers settings

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

REST is selected explicitly with `--transport rest`; it is not started alongside MCP. These settings provide defaults when that transport is selected.

| Field | Type | Default |
|---|---|---|
| `rest_host` | `str` | `127.0.0.1` |
| `rest_port` | `int` | `8080` |
| `rest_base_path` | `str` | `/v1` |

`rest_base_path` is validated to begin with `/`.

### `llm_model_name` and chat

Text preparation, domain classification, and assisted source planning use the model named by `llm_model_name`. Its provider entry holds the endpoint and credentials:

```toml
[providers.local_ollama]
provider = "ollama"
base_url = "http://localhost:11434"
api_key = "…"

[models.chat_model]
provider = "local_ollama"
model = "qwen3:8b"
structured_output = true

[tools.groundworkers]
llm_model_name = "chat_model"
```

Set `structured_output = true` only for a model that supports structured responses. Model capabilities are explicit declarations, not tests of the provider. Configure the provider and select the model in **Chat Model**, then use **Chat** to check chat completion and structured output.

For an embedding model, declare `embeddings = true`. Its dimensions and any `document_prefix` or `query_prefix` must match the model and stored vectors. The embedding setup flow helps select these settings; **Check model** tests provider encoding and checks it against the store registration.

### Grounding (`grounding_*`)

These settings control Groundworkers' grounding policy. Other graph traversal limits are supplied with individual tool calls.

| Field | Type | Default |
|---|---|---|
| `grounding_min_fulltext_overlap` | `float` | `0.0` |
| `grounding_max_depth` | `int` (1-10) | `5` |

`grounding_min_fulltext_overlap` must be between `0.0` and `1.0`. It is the minimum proportion of query tokens that must appear in a matched concept name for a full-text hit to be accepted; below the threshold grounding falls through to the next tier.

`grounding_max_depth` bounds the hierarchy distance between a grounding candidate and a required parent concept, or the identity-hop count when grounding runs without `parent_ids`.

### Concept flag contract

Grounding results carry strict OMOP flags:

| Field | Meaning |
|---|---|
| `standard_concept` | true only for raw `standard_concept = 'S'` |
| `classification_concept` | true only for raw `standard_concept = 'C'` |
| `is_active` | `invalid_reason` unset, treating blank and whitespace-only as active |

Grounding can legitimately land on a classification concept (an ATC or CPT4 hierarchy node). Those are valid hierarchy positions but **not** valid mapping targets for a CDM entity field, so check the flags rather than assuming every result is standard.

`concept_ground` also accepts `standard_only` and `active_only` for each call; both default to `false`. They filter the candidates considered during resolution. In this resolver, `standard_only` admits both `S` and `C` candidates, so it does not guarantee that every returned concept has `standard_concept = true`.

### Source planning (`source_planning_*`)

| Field | Type | Default |
|---|---|---|
| `source_planning_llm_assisted_enabled` | `bool` | `true` |

When enabled and a chat model is configured, assisted source planning can use that model to classify ambiguous columns. Turning this off leaves deterministic source planning available. It does not disable other chat-backed tools.

### Knowledge packs (`knowledge_*`)

| Field | Type | Default |
|---|---|---|
| `knowledge_packs_root` | `str \| null` | `null` |

`groundworkers` includes bundled baseline knowledge packs as part of the package. Set `knowledge_packs_root` when you want to add site-specific or localisation packs on top of that baseline.

Point `knowledge_packs_root` directly to the directory containing the layer directories:

```toml
[tools.groundworkers]
knowledge_packs_root = "./my-knowledge/packs"
```

```text
my-knowledge/
  packs/                 # knowledge_packs_root points here
    localisation/
      nacc-uds-v4/
        manifest.yaml
        guidance.md
```

Relative paths resolve against the TOML file's directory; `~` is expanded. A configured pack with the same `layer` and `name` replaces the bundled copy. Other bundled packs remain available. These knowledge packs are separate from prompt workflows supplied by plugins; see [Working with prompts](prompt-handling.md).

<a id="semantic_projection"></a>

### Semantic projection (`semantic_projection_*`)

| Field | Type | Default |
|---|---|---|
| `semantic_projection_enabled` | `bool` | `false` |

Enable this to expose the `semantic_project` MCP tool. Projection uses a grounded concept and source context to produce CDM rows; it needs no embedding or chat model.

```toml
[tools.groundworkers]
semantic_projection_enabled = true
```

## What becomes available at runtime

Configuration determines which tools are registered. Registration does not establish that a database, model endpoint, or store is ready: check connections, model compatibility, and coverage separately in the console.

### With `cdm_db` alone

Concept lookup, lexical search and grounding, graph and mapping tools, deterministic source planning, knowledge packs, and system tools are available. Some search and graph operations need prepared indexes or supporting objects. No separate `[tools.omop_graph]` section is needed by the core runtime.

### With `vector_store_name` alone

Stored-vector operations remain available without a live embedding model. For example, `embedding_neighbours` can find neighbours of a concept already in the store. Supply `model_name` when needed to select the stored model.

Live text encoding, text-based embedding search, and the graph's embedding grounding tier need `embedding_model_name` as well.

### With `embedding_model_name` **and** `vector_store_name`

Both references enable live embedding search and embedding-backed grounding and mapping. The provider must be reachable and compatible vectors must already exist in the store. With a model reference alone, embedding store operations remain unavailable.

Groundworkers opens the store read-only for queries, status, and coverage checks. Store initialization and population are explicit write operations. Searches never populate missing vectors. See [Initial local setup](../from-scratch.md#5-add-embeddings-optional) for the preparation steps.

The setup wizard uses the `groundworkers` schema for PostgreSQL vector storage. The model registry and configuration provenance use their packages' reserved schemas. SQLite uses its database file without a schema. Each database supports one store per model: reuse an existing registration or choose a different database for another store of that model.

A `LEGACY_REGISTRY` error means the store layout is incompatible. Groundworkers does not migrate legacy stores automatically; inspect the diagnostic and use the migration guidance for your installed storage package. Avoid treating a registry error as an empty store.

### With `groundworkers.llm_model_name` configured

Text preparation and domain classification tools are registered. Assisted source planning is also available when `source_planning_llm_assisted_enabled = true`. The embedding model is independent of this choice.

### With `semantic_projection_enabled = true`

The `semantic_project` MCP tool is registered. Projection is deterministic and uses no model. See [Semantic projection](../tools/semantic_projection.md) for its inputs and results.

## Plugin configuration

Installed plugins can have their own `[tools.<plugin_name>]` settings and references to shared resources. They may provide console configuration pages or additional setup steps. Install plugins in the Groundworkers environment, follow their configuration guide, and restart the host after installation or configuration changes.

Use `groundworkers --describe` to check which plugins and tools were registered. Plugins that provide readiness checks can report missing configuration or dependencies in the console. Plugin settings belong to the plugin; they are not additional fields in `[tools.groundworkers]`.

## CLI selection rules

MCP uses the configured `mcp_*` defaults unless you override them on the command line. REST is selected explicitly and uses the `rest_*` defaults. `--host` and `--port` override the bind address for the chosen HTTP transport.

```bash
groundworkers --config-path /path/to/config.toml --describe
groundworkers --transport streamable-http --host 127.0.0.1 --port 8000
groundworkers --transport rest --host 127.0.0.1 --port 8080
```

Use separate TOML files for separate environments, selected with `--config-path` or `OA_CONFIG_PATH`. See [Integrations](integrations.md) for client examples.

## Direct Python example

`build_app_config()` loads the active shared stack. Supply `config_path` to select a file explicitly, or use `build_app_config_from_stack(stack)` when your application already has a `StackConfig` object.

The Python helpers do not load `groundworkers.env`; pass `config_path` explicitly if you rely on a location remembered by the console.

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config(config_path="/path/to/config.toml")
app = build_application(config)

grounding = app.services.grounding
mapping = app.services.mapping
```

Build the application once and reuse it. Optional services such as `app.services.text` are `None` when they are not configured. A service object being present does not prove its external dependencies are reachable.

`config.describe()` returns a redacted summary keyed by `database`, `model`, `llm_model`, `vector_store`, `stack`, `groundworkers`, and `app_name`. CDM schema keys are `cdm_schema` and `vocab_schema`; generic/vector-store databases continue to use `schema_name`. Passwords and API keys are masked; safe URLs never carry credentials.
