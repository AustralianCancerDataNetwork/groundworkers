# Initial local setup

This is the supported fresh-local setup for Groundworkers. It assumes an OMOP CDM database whose vocabulary tables are already populated. Groundworkers does not load vocabulary data or silently create embedding stores.

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

## Install

Install the setup console and the capabilities you intend to use:

```bash
uv pip install "groundworkers[tui,embedding-pgvector]"
```
## Run the setup console

```bash
groundworkers tui
```

With no configuration, the console shows the default destination and opens CDM setup. The Overview separates required CDM readiness from optional graph, embedding, chat, and integration status.

### Required: CDM database

Configure the CDM connection and logical database. The review shows the exact redacted `[connections.*]`, `[databases.*]`, and `[tools.groundworkers]` changes that will be applied. The console preserves the shared oa-configurator entries, references, ownership, and revision checks; it does not replace them with a Groundworkers-specific schema.

Run **Test connections** or **Overview → Verify all**. A connected CDM with populated vocabulary tables is the minimum usable service. Missing optional embeddings or chat remain neutral and do not make the core service fail.

**Note** We assume that you will be connecting to a CDM that *already has vocabulary tables populated*.

### Recommended: graph and search

Select **Graph → Prepare graph** when readiness diagnostics identify missing relationship, full-text, or functional indexes. The operation is an ordered, persistent local maintenance run. Use **Performance** to review index readiness in one place and, where supported, start Groundworkers trigram or embedding-index maintenance.

### Optional: embeddings

Embedding operations are currently separate setup actions:

1. select the unconfigured embedding row under **Database** and configure the vector store;
2. open **Embeddings** and configure the embedding provider and model;
3. initialize the vector store explicitly;
4. run **Check model**, then refresh coverage;
5. start a persistent population run for the reviewed scope.

A numeric limit caps a run; it does not define whether the intent is a backfill. The Runs section exposes controls only when the selected run supports them.

### Optional: chat model

The **Chat Model** section configures separate named `[providers.*]` and `[models.*]` entries. The model is discovered from the configured endpoint, and credentials remain redacted in reviews and diagnostics.

## Integration output

After the required CDM capability is connected, choose **Overview → Show integration output**. The console provides exact commands for both supported MCP styles:

```text
groundworkers --config-path /path/to/config.toml --transport stdio
groundworkers --config-path /path/to/config.toml --transport streamable-http --host 127.0.0.1 --port 8000
```

The output contains no rendered TOML or credentials. REST is a separate explicit CLI transport (`--transport rest`), not an alongside-MCP switch.

## Copied-in configuration

To inspect a deployment-managed or copied configuration without editing it:

```bash
groundworkers tui --config-path /path/to/config.toml --config-read-only
```

The console reports the read-only ownership and keeps mutation controls disabled. Change the authoritative source and reopen the console to verify the result.

## Recovery and durable runs

If the selected configuration is missing or malformed, run:

```bash
groundworkers tui --config-path /path/to/config.toml
```

Maintenance state is stored under `$GROUNDWORKERS_STATE_HOME`, then `$XDG_STATE_HOME/groundworkers`, or the platform state default. Set the first variable to a persistent container mount so graph and embedding runs survive a restart.
