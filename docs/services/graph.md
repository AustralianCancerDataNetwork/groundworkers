# GraphService

`GraphService` is the direct Python surface for deterministic graph-backed
concept operations. It sits above `OmopGraphAdapter` and gives Python callers
the same stable graph workflow layer used by the MCP concept tools.

## Construction

`GraphService` is wired by `build_application(...)` when the omop-graph backend
is available.

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

graph = app.services.graph
```

`graph` is `None` when the omop-graph backend is not configured.

## What it is for

Use `GraphService` when you want:

- deterministic concept lookup from identifiers
- hierarchy traversal with explicit depth limits
- relationship summaries and neighbor exploration
- shortest-path and equivalency-path queries
- standard mapping from source vocabulary codes

If you need caller-facing free-text grounding policy, use
`ConceptGroundingService`. If you need lexical search over the shared vocabulary
tables, use `VocabService`.

## Core methods

### `get_concept(...)` and `get_concept_by_code(...)`

Return a serialized OMOP concept from a known identifier.

### `get_ancestors(...)` and `get_descendants(...)`

Walk the OMOP hierarchy with bounded depth and serialized results.

### `get_edges(...)` and `get_neighbors(...)`

Expose direct relationships or a bounded multi-hop neighborhood for one concept.

### `find_path(...)` and `find_equivalency_path(...)`

Return shortest graph paths, either across all predicate kinds or restricted to
identity-driven equivalence traversal.

### `map_to_standard(...)`

Resolve a vocabulary code to standard OMOP concept targets.

## Relationship to transports

The concept MCP tools delegate to this service. If you are already in Python,
prefer `app.services.graph` over importing tool modules or calling adapter
internals directly.
