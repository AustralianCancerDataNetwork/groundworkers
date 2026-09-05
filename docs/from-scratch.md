# Initial local setup

This guide sets up a local Groundworkers runtime against an existing OMOP CDM database. The vocabulary tables must already be populated; Groundworkers does not load vocabulary data.

The minimum useful configuration is a CDM database. Graph indexes, embeddings, and a chat model add capabilities but are independent of the core vocabulary service.

### Current Status

The TUI is offered for convenience and should be considered experimental - we don't persist state centrally so you will probably notice that you need to re-test connections if you move around the interface.

The examples below show the most thoroughly validated workflow at this time. FAISS and sqlite options for storage and anything other than ollama configuration are all yours to experiment with.

### Known TODOs

- **Plugin-backed optional capabilities:** Finalise the drafted plugin contract for adding backends without expanding the core composition root for every integration. A plugin should declare its configuration schema and adapter/service factories through an explicit entry point. Groundworkers should retain lifecycle, error, and transport policy. Backend-specific assumptions must remain inside the plugin rather than leaking into generic services.
- **Async concurrency hardening:** Model-facing MCP tools use omop-llm's native async completion, embedding, and availability APIs. Before increasing request concurrency, cover lazy initialization, provider connection limits, cancellation, and shared-backend safety with load tests.
- **Curated REST workflows:** Extend REST around stable, application-level workflows rather than mirroring every MCP tool. New endpoints need typed request/response models, the same validation and error semantics as the underlying services, explicit limits for expensive operations, and a deployment-level authentication decision before exposure outside a trusted network.

### Key Dependency Details

- **FastAPI** provides the curated REST transport, including request validation, exception mapping, OpenAPI generation, and application lifecycle hooks. REST is selected explicitly with `--transport rest`; it is not served alongside MCP by default. Business logic belongs in services, not route handlers.
- **FastMCP**, supplied by the official `mcp[cli]` package, provides MCP tool, prompt, and resource registration plus stdio, SSE, and streamable-HTTP transports. Groundworkers preserves each handler's signature and docstring for schema discovery and registers model-facing handlers as native async tools.
- **omop-llm** is the provider-neutral model API. MCP-facing provider calls use its async completion, embedding, and availability methods so a cached provider client remains attached to the transport's persistent event loop. The synchronous methods remain available to direct Python callers.
- **omop-emb and omop-graph** remain synchronous storage and graph dependencies. Their database and vector operations must stay outside the event-loop thread until those packages expose supported async contracts.

### Maintenance tasks

Long running tasks (like embedding population) will be spawned and polled, and you can review progress on the `Runs` tab. These tasks should persist even if you close and re-open groundworkers because they are spawned as maintenance tasks. This is all best-effort at this time, with a primary goal of supporting a centralised configuration workflow only, not being an actual groundworkers interface for real agentive work.

Open **Runs** to follow progress, inspect a safe log tail, cancel active work, retry a safe failed step, or export commands. Postflight is shown only for plans that actually define postflight checks.

## 1. Install

Install the setup console and the optional backends you plan to use:

```bash
uv pip install "groundworkers[tui,embedding-pgvector]"
```

The `tui` extra is needed for the setup console. Embedding backend selection is via extras either `embedding-faiss` / `embedding-pgvector`. Use the `all_source` extra for XLSX, PDF, and DOCX source-planning inputs.

## 2. Open the setup console

```bash
groundworkers tui
```

To work with a specific file:

```bash
groundworkers tui --config-path /path/to/config.toml
```

The console shows required CDM readiness separately from optional graph, embedding, chat, and integration status. Before applying a change, review the redacted configuration diff.

## 3. Configure the CDM

Configure the physical connection and the logical CDM database. The setup flow writes the shared `[connections.*]`, `[databases.*]`, and `[tools.groundworkers]` entries; Groundworkers references those entries by name.

Run **Test connections** or **Overview → Verify all**. A connected CDM with populated vocabulary tables is the minimum runtime needed for concept lookup, search, grounding, mapping, source planning, knowledge packs, and system status.

## 4. Prepare graph and search support

Open **Graph → Prepare graph** when readiness reports missing relationship, full-text, or functional indexes. The operation runs as a durable maintenance task. Use **Performance** to inspect index readiness and start supported index maintenance.

Graph preparation is not required for every CDM-only operation, but `concept_ground` and several concept and mapping tools use graph-backed operations when available.

## 5. Add embeddings (optional)

Embedding search needs all three of these pieces:

- a named embedding model;
- a named vector store;
- populated vectors for the intended vocabulary scope.

Configure the vector store under **Database**, configure the model under **Embeddings**, initialize the store, and run the population task. Population is explicit and does not start during a query. A run limit caps one maintenance run; it does not decide whether the run is a backfill.

## 6. Add a chat model (optional)

Configure a provider and a structured-output chat model in **Chat Model**. Chat enables text preprocessing, structured-field domain classification, and assisted source planning. Credentials are redacted in reviews and diagnostics.

## 7. Start or inspect the service

After setup, inspect the active runtime:

```bash
groundworkers --config-path /path/to/config.toml --describe
```

Start MCP over stdio for a client that launches Groundworkers:

```bash
groundworkers --config-path /path/to/config.toml --transport stdio
```

Start a shared Streamable HTTP service:

```bash
groundworkers \
  --config-path /path/to/config.toml \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

REST is a separate explicit transport:

```bash
groundworkers \
  --config-path /path/to/config.toml \
  --transport rest \
  --host 127.0.0.1 \
  --port 8080
```

The `--describe` output shows the exact active tools, prompts, resources, plugins, and safe configuration diagnostics. Optional surfaces are omitted when their prerequisites are unavailable.

## Configuration managed elsewhere

To inspect a deployment-managed or copied configuration without editing it:

```bash
groundworkers tui \
  --config-path /path/to/config.toml \
  --config-read-only
```

Change the authoritative configuration source, then reopen the console to verify the result.

## Durable maintenance runs

Graph and embedding preparation tasks appear in **Runs**. From there you can follow progress, inspect a safe log tail, cancel an active task, retry a supported failure, or export commands.

Set `GROUNDWORKERS_STATE_HOME` to a persistent directory when running in a container. This keeps maintenance state across process restarts. The fallback is `$XDG_STATE_HOME/groundworkers` or the platform state directory.
