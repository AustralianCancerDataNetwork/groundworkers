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
| `embedding-pgvector` | pgvector embedding backend support |
| `embedding-faiss` | FAISS sidecar acceleration for embedding search |
| `xlsx` | XLSX source-planning input support |
| `pdf` | PDF source-planning input support |
| `docx` | DOCX source-planning input support |
| `all_source` | XLSX, PDF, and DOCX source-planning input support |
| `dev` | Test, lint, docs, and local development tooling |

Examples:

```bash
pip install "groundworkers[tui,embedding-pgvector]"
pip install "groundworkers[all_source]"
```

## Shared stack prerequisite

`groundworkers` runs against the shared OMOP stack config managed by
`oa-configurator`. In a typical setup you configure:

```bash
omop-config configure groundworkers
```

That writes the `[tools.groundworkers]` section and the `[connections.*]` and
`[databases.*]` entries it references. Embedding-backed capabilities need two
further named entries — a `[models.*]` embedding model and a `[vector_stores.*]`
store — which the setup console can create for you:

```bash
groundworkers --tui
```

Groundworkers reads only its own package section plus the named entries it
references. It does not read `[tools.omop_graph]` or `[tools.omop_emb]`.

By default the runtime reads `~/.config/omop/config.toml`.

You can override that at startup with:

- `OA_CONFIG_PATH`
- `groundworkers --config-path ...`

There is no profile selection; use separate config files instead.

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

The REST routes are:

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
