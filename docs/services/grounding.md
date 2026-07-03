# ConceptGroundingService

`ConceptGroundingService` is the direct Python surface for free-text grounding
policy over `GraphService`. It decides how grounding requests are constrained
and which resolver tiers participate; the graph service then executes the plan
against the omop-graph backend.

## Construction

`ConceptGroundingService` is wired by `build_application(...)` when
`GraphService` is available.

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

grounding = app.services.grounding
```

`grounding` is `None` when the omop-graph backend is not configured.

## What it is for

Use `ConceptGroundingService` when you want:

- one ranked grounding workflow for free text
- domain and vocabulary constraints applied consistently
- optional ancestry constraints via `parent_ids`
- an explanation of which resolver tier matched

## Core method

### `ground(...)`

Runs the tiered grounding pipeline and returns:

- `results`: ranked serialized concepts with grounding scores
- `grounding_explanation`: matched tier, embedding usage, and the applied ancestry constraint

When `parent_ids` is omitted, the search runs without an ancestry constraint.
When `parent_ids` is provided, results must be descendants of at least one
listed concept.

## Relationship to transports

The `concept_ground` MCP tool delegates to this service. If you are already in
Python, prefer `app.services.grounding` over calling the tool wrapper directly.
