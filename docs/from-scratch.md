# Initial local setup

This is the supported fresh-local setup for Groundworkers. It assumes an OMOP CDM database whose vocabulary tables are already populated. Groundworkers does not load vocabulary data or silently create embedding stores.

## Install

Install the setup console and the capabilities you intend to use:

```bash
uv pip install "groundworkers[tui,embedding-pgvector]"
```

Add `embedding-faiss` when you also want the optional FAISS query cache; it is not a replacement vector-store backend. Add `all_source` when source-planning file formats are needed.

## Run the setup console

```bash
groundworkers tui
```

With no configuration, the console shows the default destination and opens CDM setup. The Overview separates required CDM readiness from optional graph, embedding, chat, and integration status.

### Required: CDM database

Configure the CDM connection and logical database. The review shows the exact redacted `[connections.*]`, `[databases.*]`, and `[tools.groundworkers]` changes that will be applied. The console preserves the shared oa-configurator entries, references, ownership, and revision checks; it does not replace them with a Groundworkers-specific schema.

Run **Test connections** or **Overview → Verify all**. A connected CDM with populated vocabulary tables is the minimum usable service. Missing optional embeddings or chat remain neutral and do not make the core service fail.

### Recommended: graph and search

Select **Graph → Prepare graph** when readiness diagnostics identify missing relationship, full-text, or functional indexes. The operation is an ordered, persistent local maintenance run. Open **Runs** to follow progress, inspect a safe log tail, cancel active work, retry a safe failed step, or export commands. Postflight is shown only for plans that actually define postflight checks.

### Optional: embeddings

Embedding operations are currently separate setup actions:

1. select the unconfigured embedding row under **Database** and configure the
   vector store;
2. open **Embeddings** and configure the embedding provider and model;
3. initialize the vector store explicitly;
4. run **Check model**, then refresh coverage;
5. start a persistent population run for the reviewed scope.

A numeric limit caps a run; it does not define whether the intent is a backfill. The Runs section exposes controls only when the selected run supports them. Index maintenance remains operator-managed guidance in this release.

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
