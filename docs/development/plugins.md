# Plugin architecture

Groundworkers plugins are independently installed packages that add an adapter, service, and MCP surface without adding plugin-specific imports to Groundworkers. They register one ready-to-use plugin object under the `groundworkers.plugins` entry-point group.

```toml
[project.entry-points."groundworkers.plugins"]
my_plugin = "my_package.plugin:plugin"
```

The object implements `GroundworkersPlugin`: a stable `name`, an optional `PackageConfigBase` class, `build(context, config)`, and `register(server, state)`. The plugin name and `config_cls.tool_name` must match. Installed plugin names must be unique.

`build()` receives `PluginContext`, not Groundworkers `AppConfig`. The context provides the resolved CDM database and engine, vector store, shared lazy model backend factories, and a deliberately narrow resolver for independent named OA resources. Return `None` when a prerequisite is unavailable. Groundworkers then keeps serving its core capabilities and reports why the plugin was not activated.

`register()` adds MCP tools or resources against the state returned by `build()`. The shared MCP registration wrapper supplies the same safe error translation used by core tools.

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

Schemas requiring live discovery or a conditional flow that Tier A cannot express may additionally implement the runtime-checkable `GroundworkersPluginConfigUI` protocol. Its `tui_workflow(operation)` method returns a custom `(ConfigWorkflowSpec, ConfigMutationService)` pair. Keep Groundskeeping in the plugin's optional TUI dependencies so headless plugin imports remain lightweight.

## Lifecycle and diagnostics

Groundworkers discovers the plugin set once per application build, validates its identities, resolves each package configuration, and builds state. MCP registration uses those same discovered objects. `groundworkers --describe` lists active plugins and safe `plugin_issues` for plugins skipped because their configuration, prerequisites, build, or registration failed.

Plugin packages should test their own entry-point metadata and runtime capability. Groundworkers tests the host contract with in-process fixtures and separately tests the generic configuration journey with a test-local package schema; plugin business functionality is not required to prove the host workflow.