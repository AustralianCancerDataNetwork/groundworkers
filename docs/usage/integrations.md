# Integrations

Groundworkers exposes the same configured capability layer through MCP, REST, and direct Python. Choose the interface for the caller, not for a different implementation:

| Caller or requirement | Interface |
|---|---|
| Agent needs discoverable tools, prompts, and resources | MCP |
| Application needs a small, stable HTTP workflow contract | REST |
| Python process owns batching, evaluation, or orchestration | Direct Python |

See [Concepts and capability choices](../concepts.md) for what the capabilities mean. This page shows how to start each interface.

## MCP

### Start the server

Use stdio when the MCP client starts the process itself:

```bash
groundworkers
```

Use Streamable HTTP for a shared service:

```bash
groundworkers \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000
```

The older `sse` transport is also accepted. `--describe` loads the selected configuration, builds the runtime, and prints the active tools, prompts, resources, and redacted configuration without starting a server:

```bash
groundworkers --describe
```

The available tool surface depends on configuration. For example, embedding tools need an embedding store, text and domain tools need a chat model, knowledge tools need a packs root, and semantic projection is explicitly opt-in.

### Call a tool

The exact client code depends on the MCP SDK. A representative structured call is:

```python
bundle = mcp_client.call_tool(
    "concept_candidate_bundle",
    {"query": "type 2 diabetes", "domain": "Condition"},
)
```

The response contains retrieval channels, a deduplicated `candidate_union`, optional standard mappings or context, and warnings. Use `groundworkers --describe` or MCP discovery rather than assuming every optional tool is present.

## REST

REST is deliberately curated. It currently exposes candidate bundles and assisted source planning rather than mirroring the MCP surface.

### Start the server

```bash
groundworkers \
  --transport rest \
  --host 127.0.0.1 \
  --port 8080
```

The default routes are:

- `GET /healthz`
- `POST /v1/mapping/candidate-bundle`
- `POST /v1/source-planning/assisted-plan`

The `/v1` prefix is configurable with `rest_base_path`. The current REST transport has a placeholder authentication dependency and does not provide authentication itself; place it behind the deployment's authentication and network controls before exposing it beyond a trusted boundary.

### Candidate bundle

```bash
curl -X POST http://127.0.0.1:8080/v1/mapping/candidate-bundle \
  -H 'content-type: application/json' \
  -d '{
    "query": "type 2 diabetes",
    "domain": "Condition"
  }'
```

The request model validates and bounds its limits. Backend failures are returned as JSON error objects with an HTTP status appropriate to the error category.

### Assisted source planning

```bash
curl -X POST http://127.0.0.1:8080/v1/source-planning/assisted-plan \
  -H 'content-type: application/json' \
  -d '{
    "content": "field_name,field_label\nhba1c,Haemoglobin A1c\n",
    "filename": "dictionary.csv",
    "caller_hint": "data_dictionary"
  }'
```

Set `content_encoding` to `base64` when sending binary-safe encoded content. Set `include_intermediate` when the caller needs the source-planning stages for inspection.

## Direct Python

Resolve configuration and build one application at startup:

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config(config_path="/path/to/config.toml")
app = build_application(config)

mapping = app.services.mapping
if mapping is None:
    raise RuntimeError("The CDM vocabulary runtime is not configured.")

bundle = mapping.concept_candidate_bundle(
    "type 2 diabetes",
    domain="Condition",
)
```

Use `app.services.vocab` for lexical retrieval, `app.services.graph` for deterministic concept operations, `app.services.grounding` for tiered free-text grounding, `app.services.mapping` for review-oriented workflows, `app.services.text` and `app.services.domain` for model-assisted preprocessing, and `app.services.source_planning` for stateless source analysis. Optional services are `None` when their prerequisites are absent.

Direct services return normal Python values or typed models. They raise `GroundworkersError` or `ValueError`; they do not return MCP error dictionaries. The transport boundary is responsible for serialization and error translation.

`SemanticProjectionService` is constructed directly because it needs no database, embedding store, or model:

```python
from groundworkers.services.semantic_projection import SemanticProjectionService

projector = SemanticProjectionService()
```

Build the runtime once and reuse it. Adapters own connection pools and lazy backend state; repeatedly constructing applications can create unnecessary resources.

## Relationship to groundcrew

`groundcrew` normally consumes Groundworkers over MCP. It owns session state, job lifecycle, and orchestration policy; Groundworkers owns reusable, stateless domain capabilities. A Python application can use the worker services directly when it does not need a remote tool boundary.
