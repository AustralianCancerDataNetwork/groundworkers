# Plugin architecture

For an overview and examples, start with [Plugins](../plugins.md). This guide
covers the implementation contract for plugin authors.

## What a plugin is

Groundworkers plugins are independently installed packages that add an adapter, service, and MCP surface without adding plugin-specific imports to Groundworkers. They register one ready-to-use plugin object under the `groundworkers.plugins` entry-point group.

```toml
[project.entry-points."groundworkers.plugins"]
my_plugin = "my_package.plugin:plugin"
```

```mermaid
flowchart LR
    subgraph PKG["my_package — a separate installed package"]
        direction TB
        CFG["Config class<br/><i>the settings this plugin needs</i>"]
        OBJ["Plugin object<br/>name &middot; config class &middot; build() &middot; register()"]
    end

    subgraph HOST["Groundworkers — the host application"]
        direction TB
        DISC["Discovery<br/><i>looks up installed plugins</i>"]
        CTX["Shared connections<br/><i>database, embeddings — already<br/>set up by Groundworkers itself</i>"]
        SRV["Shared MCP tool server"]
    end

    OBJ -.->|"found via the environment's<br/>own package listing"| DISC
    DISC -->|"reads + validates"| CFG
    CTX -->|"handed to"| OBJ
    OBJ -->|"register() adds its tools"| SRV
```

The object implements `GroundworkersPlugin`: a stable `name`, an optional `PackageConfigBase` class, `build(context, config)`, and `register(server, state)`. The plugin name and `config_cls.tool_name` must match. **Installed plugin names must be unique.**

```python
class MyPlugin:
    name = "my_plugin"
    config_cls = ExampleConfig  # or None, if this plugin needs no configuration

    def build(self, context, config):
        ...  # return a service object, or None if a prerequisite is unavailable

    def register(self, server, state):
        ...  # add this plugin's MCP tools/resources


plugin = MyPlugin()
```

## Start from the use case

A plugin's configuration should follow from what it needs to be able to complete its task. 

Work through each requirement before writing the class.

For example, a plugin recommending comparator cohorts might reason like this.

**Reuse a resource Groundworkers already resolved.** Nothing to declare — read it straight off `PluginContext` inside `build()`:

| Use case | What you need | How you get it |
|---|---|---|
| Look up existing OMOP concepts | Read access through Groundworkers' routed vocabulary session | `context.cdm_session_factory` / `context.resolver` |
| Rank or filter by similarity to concepts Groundworkers already embedded | The concept embeddings Groundworkers already populated, via omop-emb | `context.vector_store` + `context.embedding_backend_factory` |
| Ask an LLM to help decide something | The chat model Groundworkers already configured, via omop-llm | `context.chat_backend_factory` |

**Create something this plugin owns.** Each becomes a field on its own `PackageConfigBase` — see [Package configuration](#package-configuration):

| Use case | What you need | Configuration field established | Notes | 
|---|---|---|---|
| Store its own distinct non-CDM source tables | A database connection this plugin owns and writes to | `comparator_db: Annotated[str, RefTo(CDMDatabaseConfig)] = "cdm_db"` | Same shape as a core reference, but the operator points it at a new entry when they configure the plugin |
| Vectorised *arbitrary free text or objects* for similarity search | Its own embedding model | `embedding_model_name: Annotated[str \| None, RefTo(ModelConfig)] = None` | Optional, independent of Groundworkers' own model choice |
| Store and search those vectors | Its own vector index | `vector_store_name: Annotated[str \| None, RefTo(VectorStoreConfig)] = None` | **Not existing OMOP concepts, see above** |
| Let operators tune how strict "similar enough" is | Parameter the plugin's logic requires | `min_databases_default: int = Field(default=2, ge=1)` | A plain setting, nothing to resolve |

The concept-embedding row and the free-text-embedding row look similar but are not the same decision: the first reuses a vector store Groundworkers already populated with OMOP concepts; the second stands up an independent embedding model and vector store for data that was never a concept to begin with. Reusing `context.vector_store` for the second case would put unrelated vectors in Groundworkers' own concept index.

### Assumptions this table is making

- **Vocabulary only, so far.** Every example above reads OMOP vocabulary: concepts, concept relationships, concept embeddings, or the host's shared grounding cascade. `context.cdm_session_factory` routes vocabulary and primary roles, including on split-vocabulary deployments. Clinical/person-level queries need a database role explicitly configured for them.
- **Shared mapping access.** `PluginContext.mapping_context` is an optional,read-only capability façade over the host's mapping and grounding policy. Use its operations when a plugin needs candidate bundles, text grounding, or concept context. `concept_shared_standard_ancestor(concept_ids, max_depth=5)` returns the unique nearest shared standard ancestor when one exists; raise `max_depth` to widen the hierarchy search. The underlying vocabulary, graph, and service objects are deliberately not exposed. Do not construct a second `omop-graph` or vocabulary adapter from `context.cdm_engine`. The field is `None` only when the host did not compose services for the plugin context.

## Build and register a new plugin

`build()` receives `PluginContext`. The context provides the resolved CDM database and primary engine, routed `cdm_session_factory`, vocabulary engine, vector store, shared lazy model backend factories, the optional read-only `mapping_context`, and a deliberately narrow resolver for independent named OA resources. Return `None` when a prerequisite is unavailable. Groundworkers then keeps serving its core capabilities and reports why the plugin was not activated.

Use `context.cdm_session_factory` for role-tagged OMOP queries. `context.cdm_engine` remains available for primary-role access. On split-vocabulary stacks, plugins must use the routed factory; an Engine-based OmopGraphAdapter or DomainNameResolver cannot route vocabulary queries and will fail with upgrade guidance.

`register()` adds MCP tools, prompts, or resources against the state returned by `build()`. The shared MCP registration wrapper supplies the same safe error translation used by core tools. Code-defined prompts can continue to derive their arguments from the callable signature. A plugin publishing data-defined prompts can pass explicit string metadata with `PromptArgument`; this keeps dynamic pack fields out of Python closure signatures:

```python
from groundworkers.base import PromptArgument

server.prompt(
    "my_pack_workflow",
    title="Run my pack workflow",
    description="Start the workflow published by my pack.",
    arguments=[
        PromptArgument(
            "request",
            "The user's starting request.",
            required=False,
        )
    ],
)(handler)
```

```mermaid
sequenceDiagram
    participant Env as Installed packages
    participant GW as Groundworkers (host)
    participant P as my_package (a plugin)

    Note over Env: 1.

    GW->>Env: At startup: list every registered plugin
    Env-->>GW: my_package's plugin object

    Note over GW,P: 2.

    GW->>P: Resolve its config class into validated settings
    GW->>P: build(shared connections, those settings)
    P-->>GW: its own service object (or "not available")

    GW->>P: register(shared MCP server, that service object)
    P-->>GW: adds its own tools to the shared server

    Note over GW: 3.
```

1. The plugin's own installer registered one entry: "here is my plugin object."
2. Groundworkers' code never imports `my_package` by name. It only expects four things on the object (`name`, optional `PackageConfigBase` class, plus `build` and `register` methods, described above).
3.  The plugin's tools are now callable through the one Groundworkers server.

## Package configuration

A plugin with scalar and `RefTo` fields receives a setup-console page without any Groundskeeping-aware plugin code:

```python
from typing import Annotated, ClassVar

from oa_configurator import CDMDatabaseConfig, PackageConfigBase, RefTo
from pydantic import Field


class ExampleConfig(PackageConfigBase):
    tool_name: ClassVar[str] = "my_plugin"

    database: Annotated[str, RefTo(CDMDatabaseConfig)] = "cdm_db"
    retries: int = Field(default=2, ge=1)
```

The generic workflow maps supported Pydantic scalars to Groundskeeping fields, offers existing references of the correct concrete type, and can create a new reference chain recursively through oa-configurator's public `plan_configure()` API. Candidates remain private to the Groundworkers provider; review output is redacted and persistence is revision-aware.

Schemas requiring live discovery or a conditional flow (and therefore not fitting the default configuration UX specification) may additionally implement the runtime-checkable `GroundworkersPluginConfigUI` protocol. Its `tui_workflow(operation)` method returns a custom `(ConfigWorkflowSpec, ConfigMutationService)` pair. Keep Groundskeeping in the plugin's optional TUI dependencies so headless plugin imports remain lightweight.

### Sensitive fields

`PackageConfigBase` inherits oa-configurator's `SecretSafeModel`. A plugin developer must mark every plugin-owned secret with `Sensitive()` so model representations, interactive fields, configuration views, and review diffs can redact it by schema declaration. Groundworkers does not infer sensitivity from field names.

```python
from typing import Annotated, ClassVar

from oa_configurator import PackageConfigBase
from oa_configurator.refs import Sensitive, Secret
from pydantic import Field


class ExampleConfig(PackageConfigBase):
    tool_name: ClassVar[str] = "my_plugin"
    api_token: Annotated[str | None, Sensitive()] = Field(default=None)
```

The `Secret` alias is equivalent to `Annotated[str | None, Sensitive()]`. Prefer a `RefTo` field when a plugin can reuse an existing database, model, provider, or vector-store entry. The plugin then stores only the logical reference name; sensitivity remains declared on the referenced schema, such as `ConnectionConfig.password` or `ProviderConfig.api_key`, and the generic recursive configuration workflow preserves those declarations.

This protects display and logging surfaces; it does not encrypt persisted configuration. Plugin documentation must describe the storage expectations for any secret it introduces.

## Upgrading plugins to Groundworkers 0.7

1. Require `oa-configurator>=2,<3` and `groundworkers>=0.7,<0.8` in the plugin package.
2. Import `Sensitive` and `Secret` from `oa_configurator.refs`, and load files with `load_stack_config(path)`.
3. Create CDM role engines with `omop_alchemy.config.create_cdm_engines(resolved, register_claims=False)` for read-only plugin access; use `context.cdm_session_factory` when Groundworkers provides the database.
4. Remove schema fields from SQLite database entries. For PostgreSQL CDM entries, use `cdm_schema`; vector-store database entries continue to use `schema_name`.
5. Keep accepting `context.cdm_engine` for integrations that need the primary SQLAlchemy engine. The routed factory handles vocabulary, primary, and results roles for ORM queries.
6. Treat `context.embedding_backend_factory()` as a read-only omop-emb 3.0 `EmbeddingStoreReader`. It is shared with Groundworkers tools and status; reader methods no longer accept `metric_type`, and model enumeration is `get_registered_models()`. Do not use it to register models, create tables, or write vectors. Use a plugin-owned store and explicit setup operation for plugin data.
7. Pass `context.cdm_session_factory` when constructing `OmopGraphAdapter`; Engine mode only works for colocated databases. `context.cdm_engine` is the primary-role engine and cannot query vocabulary on a split deployment.
8. Use the current public error codes: `LEGACY_REGISTRY`, `MISPLACED_REGISTRY`, `MISSING_STORAGE_TABLE`, `MODEL_REGISTRATION_CONFLICT`, and `READ_ONLY_STORE`.
9. Update `describe()` consumers for the `llm_model`, `stack`, `groundworkers`, and `app_name` keys and `cdm_schema` / `vocab_schema` names. The old `base.sql` schema/engine helpers have been removed; use oa-configurator's `physical_schema_of`, `qualified`, `supports_schemas`, and `schema_for_role` APIs.
10. Update direct `AppConfig` construction for the required `stack`, `groundworkers`, `cdm_database`, `cdm_engine`, `cdm_vocab_engine`, `cdm_session_factory`, `embedding_model`, `llm_model`, `vector_store`, and `knowledge_root` fields; prefer `build_app_config()` in plugins and tests.

## Setup steps

Plugin data preparation belongs in registered setup steps. A plugin may expose a `setup_steps` tuple of `PluginSetupStep` instances. Each step declares a stable key, operator copy, typed arguments, and a `build_plan(values, config_path)` callable that returns a `MaintenancePlan`.

The host provides the common setup-console form and review flow, passes argument values only to that run, and starts the resulting plan through the durable maintenance-run store. Logs, progress, cancellation, retry, and the Runs view are therefore shared by core operations and plugins.

Setup arguments are separate from package configuration: database/model references and policy defaults belong in configuration, while paths to an input export belong to the one-off setup run.

Long-running plugin work should be represented by commands in the plan and run out of process. The command may invoke the plugin's own setup module or console entry point; Groundworkers does not import plugin internals or reproduce their loading logic.

## Readiness verification

A plugin may implement `GroundworkersPluginReadiness.verify_readiness(state)` when operators need to distinguish valid package configuration from an initialised, usable capability.

Verification is read-only and returns a `PluginReadinessResult` containing a stable state, a summary, and `PluginReadinessField` values. Display values must never contain credentials, connection URLs, or raw exception text.

The contract is headless: it contains no Groundskeeping types. Groundworkers uses the same result for generic setup-console rendering, while a plugin may return `result.as_dict()` from its own MCP status tool. Plugins own dependency- specific checks; the host only builds current plugin state, handles unavailable configuration safely, and maps readiness states to existing Groundskeeping status, table, empty, action, and detail contracts.

## Configuration linting

A plugin may implement the runtime-checkable `GroundworkersPluginLinter` protocol when its settings need checks beyond the configuration schema. The hook is `lint_config(config) -> PluginLintReport`; it receives the plugin's validated `PackageConfigBase` instance, or `None` when the plugin has no config class. It runs without calling `build()`, so it can check configuration-dependent files or cross-field rules without constructing runtime services.

Return a report with the plugin name and zero or more `PluginLintDiagnostic` values. Each finding has a stable code, `error` / `warning` / `info` severity, concise message, and optional configuration path. Errors make `report.valid` false; warnings and informational findings do not. Keep diagnostics safe for display: do not include secret values, full connection strings, or raw exception text.

Headless consumers call `lint_plugin_config(plugin, config)`. It returns `None` when the optional protocol is not implemented. If a linter raises or returns a malformed report, Groundworkers logs a safe error summary and returns a generic error finding.

Run the installed plugin checks from the command line:

```bash
groundworkers lint-plugins --config-path /path/to/config.toml
```

The command prints a JSON report and exits with status 1 if any linter reports an error. Plugins without the optional hook appear under `skipped`; only plugins with the hook have their settings passed through their package schema and linter. It does not build the Groundworkers runtime or connect to OMOP services. This command covers plugin configuration only; prompt packs and Groundworkers knowledge packs should be validated by their owning subsystems.


## Lifecycle and diagnostics

Groundworkers discovers the plugin set once per application build, validates its identities, resolves each package configuration, and builds state. MCP registration uses those same discovered objects. `groundworkers --describe` lists active plugins and safe `plugin_issues` for plugins skipped because their configuration, prerequisites, build, or registration failed.
