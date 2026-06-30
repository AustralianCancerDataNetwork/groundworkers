# MappingService

`MappingService` is the main direct-Python API for mapping and adjudication
workflows. It coordinates lexical retrieval, graph context, and optional
embedding retrieval into review-friendly packets.

## Construction

`MappingService` is wired by `build_application(...)` when the shared CDM
runtime is available.

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

mapping = app.services.mapping
```

`mapping` is `None` only when the runtime could not build the shared vocabulary
layer.

## What it is for

Use `MappingService` when you want:

- multi-channel candidate retrieval for a source term
- deterministic context packets for reviewer or prompt assembly
- standard-value navigation helpers
- evaluation utilities for predicted mappings

If you only need one lexical retrieval primitive, use `VocabService`. If you
want the review-oriented orchestration layer, use `MappingService`.

## Core methods

### `concept_candidate_bundle(...)`

Builds a single response that can include:

- exact lexical results
- normalized lexical results
- full-text results
- embedding results
- standardized candidates
- optional hierarchy or relationship context

This is the main mapping-review entrypoint.

### `concept_mapping_context(...)`

Builds a deterministic context packet for one concept, including optional:

- standard mappings
- ancestors
- descendants
- relationship summary
- lexical neighbours
- embedding neighbours

This is useful after a candidate has already been selected.

### `concept_search_normalized(...)`

Exposes the normalized lexical search layer directly when you want a lighter
operation than a full candidate bundle.

### `concept_nearest_standard_ancestor(...)`

Finds a standard backoff target when the seed concept or grounded phrase lands
on a non-standard concept.

### `concept_map_to_value(...)`

Follows `"Maps to value"` links for value-domain workflows.

### `concept_resolve_mapping_expression(...)`

Resolves a short mapping expression through the available search channels in a
best-effort order.

### `mapping_evaluate_candidates(...)`

Compares predicted candidates against reference concept IDs and summarizes their
relationship to the reference set.

## Typical usage pattern

```python
bundle = mapping.concept_candidate_bundle(
    "metformin",
    domain="Drug",
    include_normalized=True,
    include_fulltext=True,
    include_embedding=True,
    include_standard_mappings=True,
)

top = bundle["candidate_union"][0] if bundle["candidate_union"] else None
if top is not None:
    context = mapping.concept_mapping_context(
        top["concept_id"],
        include_standard_mapping=True,
        include_ancestors=True,
        include_relationship_summary=True,
    )
```

## Relationship to transports

The mapping MCP tools and the REST `candidate-bundle` endpoint both delegate to
this service. If you are already in Python, prefer calling the service directly
instead of going through a transport wrapper.
