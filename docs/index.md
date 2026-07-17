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

## At a glance

```mermaid
flowchart TD
    PY[Python app] --> APP[build_application]
    MCP[MCP client] --> T[tools]
    REST[REST client] --> R[transports/rest]
    APP --> S[services/]
    T --> S
    R --> S
    S --> A[adapters/]
    A --> OG[omop-graph]
    A --> OE[omop-emb]
    A --> DB[(OMOP CDM / vocab)]
    A --> LLM[LLM API]
```

This is the conceptual shape of the package: transport choices stay thin,
reusable workflow logic lives in `services/`, and concrete dependencies are
isolated behind `adapters/`. Configuration and startup wiring are described in
[Architecture](architecture.md); the home page keeps the focus on the layer
boundaries that matter to most users.

## What groundworkers provides

- **Vocabulary and hierarchy lookup** over OMOP concepts
- **Multi-channel mapping workflows** combining exact, normalized, full-text, and embedding retrieval
- **Source planning** for stateless pre-ingest analysis
- **Knowledge-pack discovery** over bundled baseline packs plus optional configured site/localisation packs
- **LLM-backed text normalization and domain classification**
- **Thin transports** over the same service layer

## Where to start

- [Installation](usage/installation.md) for package install, stack prerequisites, and service startup
- [Configuration](usage/configuration.md) for the shared-stack config model and ownership boundaries
- [Integrations](usage/integrations.md) for MCP, REST, and direct Python usage patterns
- [Architecture](architecture.md) for composition, transport flow, and extension boundaries
- [Extending groundworkers](development/extending.md) for adding adapters, services, MCP tools, or REST endpoints

## Relation to groundcrew

`groundworkers` and `groundcrew` are intentionally separate:

- `groundworkers` owns reusable stateless capabilities
- `groundcrew` owns orchestration, session state, and job lifecycle

In the usual deployment shape, `groundcrew` talks to `groundworkers` over MCP.
If you are building your own Python application, you can call the same service
layer directly through `build_application(...)`.
