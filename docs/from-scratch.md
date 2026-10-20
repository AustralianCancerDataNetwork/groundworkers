# Initial local setup

Use this guide to connect Groundworkers to an existing OMOP CDM database and start a local service. The vocabulary tables must already be populated; Groundworkers does not load vocabulary data.

Start with the CDM connection. You can add graph indexes, embeddings, a chat model, and plugins as you need them. A chat model is not required for vocabulary lookup or lexical grounding.

## Before you start

You need Python 3.12 or newer, the database connection details, and access to the CDM and vocabulary schemas. If the vocabulary is stored separately from the clinical tables, configure its connection and schema too.

The setup console helps you edit configuration, test connections, and run preparation tasks. It does not run your agent conversations or mapping jobs. Configuration is saved to a shared TOML file; maintenance runs have their own saved history. Connection and model checks may need to be rerun after reopening the console or changing a service.

## 1. Install

In a new working directory, create and activate a virtual environment. These activation commands are for macOS and Linux:

```bash
uv venv --python 3.12
source .venv/bin/activate
```

Install Groundworkers with the setup console:

```bash
uv pip install "groundworkers[tui]"
```

If you plan to store embeddings in PostgreSQL, include its backend support:

```bash
uv pip install "groundworkers[tui,embedding-pgvector]"
```

Other extras include `embedding-faiss` for FAISS sidecar acceleration and `all_source` for XLSX, PDF, and DOCX source-planning inputs. See [Installation](usage/installation.md) for the available extras.

## 2. Open the setup console

```bash
groundworkers tui
```

The default configuration file is `~/.config/omop/config.toml`, unless `OA_CONFIG_PATH` selects another file. To choose a file explicitly:

```bash
groundworkers tui --config-path /path/to/config.toml
```

The console can help you choose a location and create or repair the configuration. Review the redacted diff before applying changes. Use the same configuration path when you later start the service.

**Overview** shows CDM readiness separately from optional graph, embedding, chat, and integration status. An unconfigured optional feature does not mean the CDM service has failed.

## 3. Configure the CDM

Choose **Overview → Configure CDM**, or open **Database**. Configure the physical connection and the logical CDM database, including the vocabulary location. The setup flow writes the shared `[connections.*]`, `[databases.*]`, and `[tools.groundworkers]` entries. Groundworkers refers to these entries by name.

Run **Test connections** in Database or **Verify all** in Overview. A working CDM connection with populated vocabulary tables supports concept lookup, lexical search and grounding, and mapping tools. Some searches and graph operations also need the preparation described below.

See [Configuration](usage/configuration.md) for the shared configuration layout and named references.

## 4. Prepare graph and search support

Open **Graph → Prepare graph** when readiness reports missing relationship, full-text, or functional indexes. Review the proposed commands before starting. **Performance** shows index readiness and offers further index maintenance.

Preparation runs as a background maintenance task; follow it in **Runs**. The database account used for preparation needs permission to create the required indexes and supporting objects. Normal service queries remain read-only.

You can use basic vocabulary operations before preparing every index. Prepare the search and graph support needed by the tools you intend to use, then rerun the checks.

## 5. Add embeddings (optional)

Embedding search needs a named embedding model, a named vector store, and vectors for the concepts you want to search. The query model must be compatible with the stored vectors.

1. **Configure the store.** Use Database to select or create the vector-store configuration.
2. **Configure the model.** In Embeddings, choose **Configure model**. Supply the provider details and select a model already registered in the store or a new model from the provider.
3. **Initialize and check.** Use **Initialize embedding store** if needed, then **Check model** to check the store registration and test whether the provider can encode text with that model.
4. **Choose the scope and populate.** Refresh coverage, select the vocabularies you need, and choose **Populate**. Review the scope, run limit, and commands before starting.
5. **Check the result.** Follow the task in Runs, then refresh coverage to see what is available.

Store initialization and population write to the embedding store. Population starts only when requested; a search never triggers it. A run limit caps the amount of work in one run. Coverage and the population plan show which concepts still need vectors.

An embedding model and a chat model are separate choices and can use different providers. Configuring one does not enable the other.

## 6. Add a chat model (optional)

Open **Chat Model**, configure the provider, and run **Test provider**. Use **Select chat model** to choose a model that supports structured output. The separate **Chat** section checks the selected model's chat and structured-output capabilities.

Chat enables text preparation and structured-field domain classification. Assisted source planning also needs `source_planning_llm_assisted_enabled` to be enabled; it is enabled by default. Provider support comes from `omop-llm`; use the console checks to verify your chosen endpoint and model. Credentials are redacted in configuration reviews and diagnostics.

## 7. Start or inspect the service

After setup, inspect what Groundworkers will expose for your configuration:

```bash
groundworkers --config-path /path/to/config.toml --describe
```

This lists registered tools, prompts, resources, plugins, and redacted configuration details. Use the console's connection and model checks to verify that the configured services are reachable too.

For an agent client that launches Groundworkers, use MCP over stdio:

```bash
groundworkers --config-path /path/to/config.toml --transport stdio
```

To run an MCP service that clients can connect to over HTTP:

```bash
groundworkers \
  --config-path /path/to/config.toml \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

REST runs as a separate transport, with selected mapping and source-planning endpoints:

```bash
groundworkers \
  --config-path /path/to/config.toml \
  --transport rest \
  --host 127.0.0.1 \
  --port 8080
```

These HTTP examples listen on the local machine. See [Integrations](usage/integrations.md) for client setup and the available interfaces. Restart a running service after changing its configuration or installed plugins.

## Optional plugins and guidance

Installed Groundworkers plugins can add tools, prompts, resources, and setup pages. Install them into the same Python environment as Groundworkers, then reopen the console or restart the service. Use `--describe` to check what was registered; plugins with readiness support also report configuration or dependency problems in the console.

For prompt packs, see [Working with prompts](usage/prompt-handling.md). Bundled knowledge packs are available without a separate plugin; local knowledge-pack paths and the optional `semantic_projection_enabled` setting are described in [Configuration](usage/configuration.md).

## Configuration managed elsewhere

To inspect a deployment-managed or copied configuration without editing it:

```bash
groundworkers tui \
  --config-path /path/to/config.toml \
  --config-read-only
```

Change the authoritative configuration source, then reopen the console to verify the result. This flag disables configuration edits; it does not make database preparation tasks read-only.

## Maintenance runs

Graph preparation and embedding population run in separate background processes. You can close and reopen the console while they continue. In **Runs**, follow progress, inspect a redacted log tail, cancel active work, retry a supported failed step, or export commands. Postflight results appear for tasks that define those checks.

Run history is saved on disk. Groundworkers uses `GROUNDWORKERS_STATE_HOME`, then `XDG_STATE_HOME`, as the state base directory and appends `groundworkers`. If neither is set, it uses `~/.local/state/groundworkers`.

In a container, set the state base to a mounted persistent directory to retain history across container replacement. An interrupted worker does not resume merely because its history survives; inspect its status in Runs before retrying.
