# Integrations

This page shows two supported downstream integration styles:

1. **MCP integration**: your application talks to `groundworkers` over the MCP
   protocol
2. **Direct Python integration**: your application imports `groundworkers` and
   calls services in-process

Both styles use the same adapter and service composition underneath.

## Choosing an integration style

Use **MCP** when:

- you want process isolation
- you want tool discovery and protocol-level interoperability
- your consumer is already an MCP client
- you want multiple clients to share the same `groundworkers` server

Use **direct Python services** when:

- your application is already Python
- you want the lowest overhead
- you want to reuse `groundworkers` logic without a transport hop
- you want to compose library calls directly inside your own codebase

## End-to-end shape

```mermaid
sequenceDiagram
    participant C as Downstream consumer
    participant M as MCP tools
    participant S as MappingService
    participant A as Adapters
    participant D as OMOP libs / DB

    alt MCP integration
        C->>M: call tool (for example concept_candidate_bundle)
        M->>S: invoke service method
    else Direct Python integration
        C->>S: invoke service method
    end
    S->>A: coordinate adapter calls
    A->>D: query libraries / DB
    D-->>A: results
    A-->>S: normalized adapter data
    S-->>M: domain result
    S-->>C: domain result
    M-->>C: MCP-safe JSON result
```

## MCP integration

### 1. Run `groundworkers` as a subprocess or service

```bash
groundworkers --config /path/to/groundworkers.yaml
```

For a shared team service:

```bash
groundworkers \
  --config /path/to/groundworkers.yaml \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000
```

For local inspection:

```bash
groundworkers --config /path/to/groundworkers.yaml --describe
```

That prints the registered tool names, signatures, and docstrings for the
current configuration.

### 2. Downstream consumer example: mapping review assistant

Imagine a downstream application that helps reviewers map source terms. Its flow
can be:

1. send the source term to `concept_candidate_bundle`
2. display the evidence channels side by side
3. fetch `concept_mapping_context` for the selected candidate
4. pass that context into an LLM or reviewer UI

The client-side call shape is MCP-client-specific, but the payload is concrete.
Pseudocode:

```python
bundle = mcp_client.call_tool(
    "concept_candidate_bundle",
    {
        "query": "type 2 diabetes",
        "domain": "Condition",
        "include_normalized": True,
        "include_fulltext": True,
        "include_embedding": True,
        "include_standard_mappings": True,
    },
)
```

Equivalent JSON arguments:

```json
{
  "query": "type 2 diabetes",
  "domain": "Condition",
  "include_normalized": true,
  "include_fulltext": true,
  "include_embedding": true,
  "include_standard_mappings": true,
  "include_hierarchy_context": true
}
```

Representative response shape:

```json
{
  "query": "type 2 diabetes",
  "constraints": {
    "domain": "Condition",
    "vocabulary_id": null,
    "standard_only": false,
    "active_only": true,
    "parent_ids": null
  },
  "channels": {
    "exact": {"available": true, "results": []},
    "normalized": {"available": true, "results": []},
    "fulltext": {"available": true, "results": []},
    "embedding": {"available": true, "results": []}
  },
  "standardized_candidates": [],
  "candidate_union": [],
  "warnings": []
}
```

The key point is that the MCP client does not need to know how exact search,
FTS, embeddings, and standardization are orchestrated. It simply calls the tool.

### 3. Fetch deterministic context for the selected candidate

```json
{
  "concept_id": 201826,
  "include_standard_mapping": true,
  "include_ancestors": true,
  "include_relationship_summary": true,
  "include_neighbors": true,
  "include_embedding_neighbors": true
}
```

This gives a downstream orchestration layer one deterministic context packet for
prompt assembly instead of forcing it to call multiple lower-level tools itself.

## Direct Python integration

### 1. Build the application container once

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.model_validate(
    {
        "omop_graph": {
            "db_url": "postgresql+psycopg://user:pass@localhost:5432/omop",
            "vocab_schema": "omop_vocab",
        },
        "omop_emb": {
            "enabled": True,
            "backend_type": "pgvector",
            "db_url": "postgresql+psycopg://user:pass@localhost:5432/omop",
            "default_model_name": "qwen3-embedding:0.6b",
            "api_base": "http://localhost:11434/v1",
            "api_key": "ollama",
        },
    }
)

app = build_application(config)
mapping = app.services.mapping
assert mapping is not None
```

In a real application, create this once at startup and keep it around rather than
rebuilding it per request.

### 2. Downstream consumer example: service-backed mapper

```python
class MappingReviewService:
    def __init__(self) -> None:
        self._mapping = mapping

    def build_review_packet(self, source_term: str) -> dict:
        bundle = self._mapping.concept_candidate_bundle(
            source_term,
            domain="Condition",
            include_normalized=True,
            include_fulltext=True,
            include_embedding=True,
            include_standard_mappings=True,
            include_hierarchy_context=True,
        )
        top = bundle["candidate_union"][0] if bundle["candidate_union"] else None
        context = None
        if top is not None:
            context = self._mapping.concept_mapping_context(
                top["concept_id"],
                include_standard_mapping=True,
                include_ancestors=True,
                include_relationship_summary=True,
                include_neighbors=True,
            )
        return {
            "source_term": source_term,
            "bundle": bundle,
            "selected_context": context,
        }
```

This is the same orchestration shape as the MCP example, just without a transport hop.

### 3. Batch or evaluation workflow example

```python
predicted = [
    {
        "source_term": "type 2 diabetes",
        "domain_id": "Condition",
        "predicted_standard_concept_ids": [201826],
    }
]
reference = [
    {
        "source_term": "type 2 diabetes",
        "domain_id": "Condition",
        "reference_standard_concept_id": 201826,
    }
]

evaluation = mapping.mapping_evaluate_candidates(
    predicted,
    reference,
    match_mode="standard_concept_id",
)
print(evaluation["summary_metrics"])
```

This is the main benefit of the service layer: downstream code can reuse the
same domain logic as MCP clients, without pretending to be an MCP client.

## MCP and direct services together

Some applications will want both:

- use direct services for in-process batch or evaluation workflows
- expose the same capabilities over MCP for interactive agents

That is the intended design direction.

Example pattern:

```python
from groundworkers.app import build_application
from groundworkers.server import create_server
from groundworkers.config import AppConfig

config = AppConfig.load("config/groundworkers.local.yaml")

app = build_application(config)
server = create_server(config)

# direct Python use
bundle = app.services.mapping.concept_candidate_bundle("hypertension")

# MCP use is available through `server`
print(server.list_tools())
```

## Which layer should a downstream app use?

Use `app.services.mapping` when you want:

- candidate bundles
- parent backoff
- mapping context
- `Maps to value`
- mapping-expression resolution
- evaluation helpers

Use `app.adapters.*` only when you explicitly want lower-level dependency-shaped
operations and are comfortable owning more orchestration yourself.

Use MCP tools when you want:

- a stable remote interface
- tool discovery for agents
- process separation or team-shared deployment

## Error handling expectations

Direct Python service calls:

- raise exceptions (`ValueError`, `GroundworkersError`, or dependency-level errors)

MCP tool calls:

- return structured error dicts:

```json
{"error": true, "code": "INVALID_INPUT", "message": "concept_id must be a positive integer"}
```

This is the boundary to keep in mind: the service layer behaves like a normal
Python API, while the tool layer behaves like a transport API.
