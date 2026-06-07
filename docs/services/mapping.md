# MappingService

`MappingService` orchestrates `VocabService`, `OmopGraphAdapter`, and `OmopEmbAdapter`
to build multi-channel candidate bundles, assemble mapping context, and evaluate
mapping predictions. It is the direct Python API for mapping and adjudication
workflows.

## Construction

`MappingService` is constructed automatically by `build_application()` when
`omop_graph` is configured. `VocabService` is required; `OmopGraphAdapter` and
`OmopEmbAdapter` are used when present.

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.load("config/groundworkers.local.yaml")
app = build_application(config)

mapping = app.services.mapping
```

`mapping` is `None` when `omop_graph` is not configured.

## Methods

### `concept_search_normalized`

```python
mapping.concept_search_normalized(
    query: str,
    domain: str | None = None,
    vocabulary_id: str | None = None,
    standard_only: bool = False,
    limit: int = 20,
) -> dict
```

Normalized lexical search returning ranked candidates. Lowercases and strips
punctuation from the query before matching. Returns a result dict with `query`,
`results`, and `warnings`.

### `concept_candidate_bundle`

```python
mapping.concept_candidate_bundle(
    query: str,
    *,
    domain: str | None = None,
    vocabulary_id: str | None = None,
    parent_ids: list[int] | None = None,
    standard_only: bool = True,
    active_only: bool = True,
    per_channel_limit: int = 10,
    overall_limit: int = 50,
    include_graph_context: bool = False,
) -> dict
```

Multi-channel candidate retrieval. Runs up to four search channels in parallel:

1. **exact** — case-insensitive exact match via `VocabService.search_exact`
2. **normalized** — normalized lexical match via `VocabService.search_normalized`
3. **fulltext** — PostgreSQL FTS via `VocabService.search_fulltext` (when available)
4. **embedding** — vector similarity via `OmopEmbAdapter.search` (when configured)

Results from all channels are de-duplicated by `concept_id`, merged, and returned
together with per-channel availability and result counts. The `constraints` block in
the response echoes back the active filters so callers can confirm what was applied.

### `concept_nearest_standard_ancestor`

```python
mapping.concept_nearest_standard_ancestor(
    concept_id: int,
    *,
    strategy: str = "nearest_standard_ancestor",
) -> dict
```

Finds the nearest standard ancestor for a non-standard concept by walking the OMOP
hierarchy upward. Returns the ancestor concept with its depth and the path taken.

### `concept_mapping_context`

```python
mapping.concept_mapping_context(
    concept_id: int,
    *,
    include_embedding_neighbours: bool = False,
    embedding_limit: int = 5,
    embedding_model: str | None = None,
) -> dict
```

Assembles a mapping context packet for a concept: its standard equivalents (via
`"Maps to"`), its value-domain equivalents (via `"Maps to value"`), its nearest
standard ancestor, and optionally its embedding neighbours. This packet is designed
for review and adjudication workflows where the caller wants multiple evidence signals
side by side.

### `concept_map_to_value`

```python
mapping.concept_map_to_value(
    concept_id: int,
) -> dict
```

Returns the value-domain standard concept(s) for a given concept by following
`"Maps to value"` relationship edges.

### `concept_resolve_mapping_expression`

```python
mapping.concept_resolve_mapping_expression(
    expression: str,
    *,
    domain: str | None = None,
    vocabulary_id: str | None = None,
) -> dict
```

Resolves a simple mapping expression (a term, code, or short phrase) to OMOP standard
concepts. Tries the channels in order (exact → normalized → FTS → embedding) and
returns the first channel that produces results, along with which channel fired.

### `mapping_evaluate_candidates`

```python
mapping.mapping_evaluate_candidates(
    candidates: list[dict],
    *,
    reference_concept_ids: list[int],
) -> dict
```

Evaluates a list of candidate concept mappings against a set of reference concept IDs.
Returns each candidate annotated with whether it matches a reference, its relationship
to reference concepts (exact, ancestor, descendant, sibling), and an overall summary.
Useful for automated mapping review workflows where a predicted mapping needs to be
checked against gold-standard or existing mappings.

## Direct Python use

```python
bundle = mapping.concept_candidate_bundle(
    "metformin",
    domain="Drug",
    standard_only=True,
    per_channel_limit=10,
)

for candidate in bundle["candidates"]:
    print(candidate["concept_id"], candidate["concept_name"], candidate["channels"])
```

The mapping MCP tools in `mapping_tools.py` delegate to these methods. When using
`groundworkers` as a Python library, call the service directly instead of going
through the MCP layer.
