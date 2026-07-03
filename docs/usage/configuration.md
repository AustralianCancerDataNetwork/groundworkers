# Configuration

`groundworkers` uses the shared OMOP stack configuration managed by
`oa-configurator`. The runtime is resolved into an `AppConfig` object by
`build_app_config(...)`; callers do not pass a separate YAML file to the
service or library layer.

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

## Configuration ownership

`groundworkers` does not own every setting it consumes. The shared stack keeps
package ownership explicit:

| Concern | Package that owns it |
|---|---|
| Shared CDM resource and schema naming | `omop-alchemy` |
| Graph traversal tuning | `omop-graph` |
| Embedding backend, model, cache, and embedding store | `omop-emb` |
| MCP defaults, REST defaults, LLM worker settings, source-planning settings, knowledge-pack settings | `groundworkers` |

This means the `groundworkers` package config stays intentionally small.

## Typical TOML shape

This is the steady-state structure to expect in `config.toml`:

```toml
[resources.cdm_db]
database = "cdm_postgres"
cdm_schema = "omop"
vocab_schema = "omop_vocab"

[tools.groundworkers]
default_resource = "cdm_db"
app_name = "groundworkers"

[tools.groundworkers.mcp]
transport = "streamable-http"
host = "127.0.0.1"
port = 8000

[tools.groundworkers.rest]
enabled = true
host = "127.0.0.1"
port = 8080
base_path = "/v1"

[tools.groundworkers.llm]
enabled = true
provider = "openai-compatible"
api_base = "http://localhost:11434/v1"
api_key = "ollama"
default_model_name = "qwen3:8b"

[tools.groundworkers.grounding]
embedding_model_name = "qwen3-embedding:0.6b"
min_fulltext_overlap = 0.5

[tools.groundworkers.source_planning]
llm_assisted_enabled = true
```

If embeddings are enabled, `omop-emb` contributes its own package section:

```toml
[tools.omop_emb]
backend = "sqlitevec"
sqlite_path = ".local/omop-emb.db"
embedding_model = "qwen3-embedding:0.6b"
api_base = "http://localhost:11434/v1"
api_key = "ollama"
```

## groundworkers-owned fields

### `app_name`

Application identity used by:

- MCP server naming
- REST application title
- runtime describe output

Default: `groundworkers`

### `mcp`

Default MCP startup settings used when you do not override them on the CLI.

| Field | Type | Default |
|---|---|---|
| `transport` | `stdio` \| `sse` \| `streamable-http` | `stdio` |
| `host` | `str` | `127.0.0.1` |
| `port` | `int` | `8000` |

### `rest`

Default REST startup settings.

| Field | Type | Default |
|---|---|---|
| `enabled` | `bool` | `false` |
| `host` | `str` | `127.0.0.1` |
| `port` | `int` | `8080` |
| `base_path` | `str` | `/v1` |

`base_path` is validated to begin with `/`.

### `llm`

Worker-owned LLM settings used by `TextService`, `DomainService`, and LLM-assisted
source planning.

| Field | Type | Default |
|---|---|---|
| `enabled` | `bool` | `false` |
| `provider` | `str` | `openai-compatible` |
| `api_base` | `str \| null` | `null` |
| `api_key` | `str \| null` | `null` |
| `default_model_name` | `str \| null` | `null` |

### `grounding`

Groundworkers-owned grounding behavior that does not belong in `omop-graph` or
`omop-emb`.

| Field | Type | Default |
|---|---|---|
| `embedding_model_name` | `str \| null` | `null` |
| `min_fulltext_overlap` | `float` | `0.0` |

`min_fulltext_overlap` must be between `0.0` and `1.0`.

### `source_planning`

| Field | Type | Default |
|---|---|---|
| `llm_assisted_enabled` | `bool` | `true` |

This controls whether `build_application(...)` wires the assisted classifier
into `SourcePlanningService` when an LLM adapter is present.

### `knowledge`

| Field | Type | Default |
|---|---|---|
| `packs_root` | `str \| null` | `null` |

Set `packs_root` to the directory containing the knowledge `packs/` tree. When
it is unset, the knowledge catalogue tools are only registered if the bundled
dev packs directory is present, so in a packaged deployment the knowledge group
is absent until `packs_root` is configured.

## What becomes available at runtime

### With only a shared CDM resource

You get:

- `CDMAdapter`
- `OmopGraphAdapter`
- `VocabService`
- `MappingService`
- concept, resolver, search, mapping, source-planning, knowledge, and system MCP tools

### With `omop_emb` configured

You additionally get:

- `OmopEmbAdapter`
- embedding MCP tools
- embedding-backed channels in `MappingService`
- optional embedding support in graph grounding

### With `groundworkers.llm.enabled = true`

You additionally get:

- `LLMAdapter`
- `TextService`
- `DomainService`
- text and domain MCP tools
- LLM-assisted source planning when `source_planning.llm_assisted_enabled = true`

## CLI selection rules

By default:

- `groundworkers` loads the active stack config file
- the active profile comes from the file unless overridden
- MCP startup uses the `mcp` defaults unless overridden on the CLI

Available runtime selectors:

```bash
groundworkers --config-path /path/to/config.toml --profile local --describe
groundworkers --transport streamable-http --host 0.0.0.0 --port 8000
groundworkers --transport rest --host 0.0.0.0 --port 8080
```

Equivalent environment overrides:

- `OA_CONFIG_PATH`
- `OA_ACTIVE_PROFILE`

## Direct Python example

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config(profile="local")
app = build_application(config)

mapping = app.services.mapping
text = app.services.text
```

Service attributes are `None` only when their prerequisites are not available in
the resolved runtime.
