# SourcePlanningService

`SourcePlanningService` provides stateless pre-ingest analysis for source artifacts. It prepares neutral planning outputs that orchestration layers can turn into ingest decisions or human review tasks.

## Construction

`SourcePlanningService` is always wired by `build_application(...)`. LLM-assisted classification is enabled only when the configured LLM backend is available and source-planning assistance is turned on in the runtime config.

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

source_planning = app.services.source_planning
```

## What it is for

Use `SourcePlanningService` when you want:

- stateless analysis of source files before ingest
- typed pre-ingest bundles for downstream orchestration
- optional LLM-assisted column-role suggestions behind the same service API

## Core methods

### `plan_source(...)`

Runs the deterministic source-planning pipeline and returns a typed pre-ingest bundle. Use this when the caller already knows the content and format context to provide.

### `plan_source_assisted(...)`

Runs the same planning flow with optional LLM-assisted column-role support when that classifier is available in the runtime. The return shape remains a pre-ingest bundle; the difference is how candidate column roles are proposed.

## Relationship to transports

The source-planning MCP tools and REST assisted-plan route delegate to this service. `groundcrew` and other orchestration layers should treat it as the reusable worker-side planning capability.
