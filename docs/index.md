# groundworkers

`groundworkers` is the reusable capability layer for OMOP-grounded lookup,
mapping, source-planning, and knowledge-pack discovery. You can use it in three
ways:

- as an **MCP service** for agentic clients and tool discovery
- as a **REST service** for controlled workflow applications
- as a **direct Python library** when you want in-process orchestration

## Choose the interface that fits your application

| Use case | Recommended interface |
|---|---|
| Agentic clients, tool discovery, shared remote service | MCP |
| Fixed request/response workflows, typed HTTP clients, OpenAPI | REST |
| In-process Python applications, batch evaluation, custom orchestration | Direct Python |

## Runtime shape

```mermaid
flowchart TD
    STACK[shared OMOP stack config] --> BOOT[build_app_config]
    BOOT --> APP[build_application]
    APP --> S[services/]
    APP --> A[adapters/]
    MCP[MCP client] --> T[tools/]
    T --> S
    REST[REST client] --> R[rest_api.py]
    R --> S
    PY[Python caller] --> S
    A --> OG[omop-graph]
    A --> OE[omop-emb]
    A --> DB[(OMOP CDM / vocab)]
    A --> LLM[LLM API]
```

## What groundworkers provides

- **Vocabulary and hierarchy lookup** over OMOP concepts
- **Multi-channel mapping workflows** combining exact, normalized, full-text, and embedding retrieval
- **Source planning** for stateless pre-ingest analysis
- **Knowledge-pack discovery** for reusable mapping and planning context
- **LLM-backed text normalization and domain classification**
- **Thin transports** over the same service layer

## Where to start

- [Installation](usage/installation.md) for package install, stack prerequisites, and service startup
- [Configuration](usage/configuration.md) for the shared-stack config model and ownership boundaries
- [Integrations](usage/integrations.md) for MCP, REST, and direct Python usage patterns
- [Architecture](architecture.md) for the runtime layers and extension boundaries
- [Extending groundworkers](development/extending.md) for adding adapters, services, MCP tools, or REST endpoints

## Relation to groundcrew

`groundworkers` and `groundcrew` are intentionally separate:

- `groundworkers` owns reusable stateless capabilities
- `groundcrew` owns orchestration, session state, and job lifecycle

In the usual deployment shape, `groundcrew` talks to `groundworkers` over MCP.
If you are building your own Python application, you can call the same service
layer directly through `build_application(...)`.
