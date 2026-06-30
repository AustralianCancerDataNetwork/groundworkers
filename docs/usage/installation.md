# Installation

## Install the package

```bash
pip install groundworkers
# or
uv pip install groundworkers
```

This installs both the Python library and the `groundworkers` CLI.

## Optional extras

Use extras to match the capabilities you plan to run:

| Extra | Enables |
|---|---|
| `llm` | LLM-backed text and domain services |
| `embedding-pgvector` | pgvector embedding backend support |
| `embedding-faiss` | FAISS sidecar acceleration for embedding search |
| `xlsx` | XLSX source-planning input support |
| `pdf` | PDF source-planning input support |
| `docx` | DOCX source-planning input support |
| `all_source` | XLSX, PDF, and DOCX source-planning input support |
| `dev` | Test, lint, docs, and local development tooling |

Examples:

```bash
pip install "groundworkers[llm,embedding-pgvector]"
pip install "groundworkers[all_source]"
```

## Shared stack prerequisite

`groundworkers` runs against the shared OMOP stack config managed by
`oa-configurator`. In a typical setup you configure:

```bash
omop-config configure omop_alchemy
omop-config configure omop_graph
omop-config configure groundworkers
# optional, only if you want embedding-backed capabilities
omop-config configure omop_emb
```

By default the runtime reads:

- `~/.config/omop/config.toml`
- the active profile declared in that file

You can override those at startup with:

- `OA_CONFIG_PATH`
- `OA_ACTIVE_PROFILE`
- `groundworkers --config-path ... --profile ...`

## Run as an MCP service

Use stdio when the caller is spawning `groundworkers` directly:

```bash
groundworkers
```

Use `streamable-http` for a shared remote service:

```bash
groundworkers \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000
```

Inspect the active runtime and registered MCP surface without starting the
service:

```bash
groundworkers --describe
```

## Run as a REST service

`groundworkers` also exposes a curated REST transport over the same service
layer:

```bash
groundworkers \
  --transport rest \
  --host 0.0.0.0 \
  --port 8080
```

The initial REST routes are:

- `POST /v1/mapping/candidate-bundle`
- `POST /v1/source-planning/assisted-plan`
- `GET /healthz`

## Use as a direct Python library

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

mapping = app.services.mapping
```

Build the application once at startup and reuse it. The same runtime object
works for direct Python calls, MCP registration, and REST startup.

## Development install

From the repository root:

```bash
uv sync --extra dev
```
