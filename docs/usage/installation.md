# Installation

```bash
pip install groundworkers
# or
uv pip install groundworkers
```

This installs the direct Python package and the `groundworkers` CLI. The same install
supports both direct-Python and MCP-server usage.

## Development install

```bash
# from the groundworkers repo root
uv sync --extra dev
```

## Optional backends

groundworkers's adapters delegate to optional companion packages. Install the extras
that match your deployment:

| Extra | What it enables |
|---|---|
| `groundworkers[embedding-tools]` | Embedding index queries backed by sqlite-vec |
| `groundworkers[embedding-pgvector]` | Embedding queries backed by PostgreSQL/pgvector |
| `groundworkers[embedding-faiss]` | Embedding queries backed by a FAISS sidecar |
| `groundworkers[all-tools]` | All tool families (sqlite-vec embeddings) |
| `groundworkers[all-tools-pgvector]` | All tool families (pgvector embeddings) |
| `groundworkers[all-tools-faiss]` | All tool families (FAISS embeddings) |

These can be combined:

```bash
pip install "groundworkers[all-tools-pgvector]"
```

## Running as an MCP server

groundworkers ships a `groundworkers` CLI entry point:

```bash
# stdio mode (used by groundcrew local transport)
groundworkers --config /path/to/groundworkers.yaml

# inspect registered tools without starting the server
groundworkers --config /path/to/groundworkers.yaml --describe
```

!!! info "HTTP mode"
    The `--describe` flag prints a JSON summary of all configured tools and their
    signatures. Use it to verify your config before connecting a client.

## Using as a direct Python library

For direct in-process use, build the shared application container:

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.load("config/groundworkers.local.yaml")
app = build_application(config)
```

Today the main direct service surface is `app.services.mapping`.
