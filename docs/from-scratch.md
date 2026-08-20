# Initial local setup

This is the supported fresh-local journey for Groundworkers 1.x. It assumes an
OMOP CDM database whose vocabulary tables are already populated. Groundworkers
does not load vocabulary data or silently create embedding stores.

## Install

Install the setup console and the capabilities you intend to use:

```bash
uv pip install "groundworkers[tui,embedding-pgvector]"
```

Use `embedding-faiss` instead of `embedding-pgvector` only when your deployment
has that supported backend. Add `all_source` when source-planning file formats
are needed.

## Run the setup console

```bash
groundworkers tui
```

With no configuration, the console states the default destination and opens the
CDM setup workflow. The Overview remains the landing page after each journey;
it separates required CDM readiness from optional graph, embeddings, chat, and
integration outcomes.

### Required: CDM database

Configure the CDM connection and logical database. The review shows the exact
redacted `[connections.*]`, `[databases.*]`, and `[tools.groundworkers]` changes
that will be applied. The console preserves the shared oa-configurator entries,
references, ownership, and revision checks; it does not replace them with a
Groundworkers-specific schema.

Run **Test connections** or **Overview → Verify all**. A connected CDM with
populated vocabulary tables is the minimum usable service. Missing optional
embeddings or chat remain neutral and do not make the core service fail.

### Recommended: graph and search

Select **Graph → Prepare graph** when readiness diagnostics identify missing
relationship, full-text, or functional indexes. The operation is an ordered,
persistent local maintenance run. Open **Runs** to follow progress, inspect a
safe log tail, cancel, retry, or run postflight verification.

### Optional: embeddings

The **Embeddings** journey keeps the model, provider, vector store, coverage,
population, and index operations distinct while presenting them together:

1. configure the embedding provider and model;
2. configure or explicitly initialize the vector store;
3. refresh coverage to see pending concepts by vocabulary;
4. start a persistent population run with one of these intents:
   **Populate from scratch**, **Backfill selected vocabularies**, or
   **Reconcile after vocabulary update**;
5. rebuild or verify indexes when the run's postflight requires it.

A numeric limit caps a run; it does not define whether the intent is a backfill.
Coverage and execution use the same selected scope.

### Optional: chat model

The **Chat Model** journey composes provider and model setup while retaining
separate named `[providers.*]` and `[models.*]` entries. The model is discovered
from the configured endpoint and credentials remain redacted in reviews and
diagnostics.

## Integration output

After the required CDM capability is connected, choose **Overview → Show
integration output**. The console provides exact commands for both supported MCP
styles:

```text
groundworkers --config-path /path/to/config.toml --transport stdio
groundworkers --config-path /path/to/config.toml --transport streamable-http --host 127.0.0.1 --port 8000
```

The output contains no rendered TOML or credentials. REST is a separate explicit
CLI transport (`--transport rest`), not an alongside-MCP switch.

## Copied-in configuration

To inspect a deployment-managed or copied configuration without editing it:

```bash
groundworkers tui --config-path /path/to/config.toml --config-read-only
```

The console reports the read-only ownership and keeps mutation controls
disabled. Change the authoritative source and reopen the console to verify the
result.

## Recovery and durable runs

If the selected configuration is missing or malformed, run:

```bash
groundworkers tui --config-path /path/to/config.toml
```

Maintenance state is stored under `$GROUNDWORKERS_STATE_HOME`, then
`$XDG_STATE_HOME/groundworkers`, or the platform state default. Set the first
variable to a persistent container mount so graph and embedding runs survive a
restart.
