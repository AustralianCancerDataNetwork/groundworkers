# VocabService

`VocabService` provides vocabulary search and concept navigation over OMOP CDM vocabulary tables. It is the direct Python API for lexical retrieval operations — the same operations exposed via the search MCP tools.

## Construction

`VocabService` takes a single `CDMAdapter` argument. It uses the adapter's session factory to open per-call database sessions.

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

vocab = app.services.vocab
```

`vocab` is `None` only when the shared CDM vocabulary runtime could not be built.

## Methods

### `search_exact`

```python
vocab.search_exact(
    query: str,
    *,
    domain: str | None = None,
    vocabulary_id: str | None = None,
    standard_only: bool = False,
    include_synonyms: bool = True,
    limit: int = 20,
) -> list[ConceptMatch]
```

Case-insensitive exact match of `query` against `concept_name` and (when `include_synonyms=True`) `concept_synonym_name`. Name matches are returned before synonym matches. Concepts that match both their name and a synonym appear only once, under `"name"`.

`standard_only` defaults to `False`. Non-standard concepts often have better lexical coverage (trade names, source codes, regional synonyms) than their standard equivalents; filtering them out in the search step loses those signals. Use `navigate_to_standard` afterward to resolve non-standard results.

### `search_normalized`

```python
vocab.search_normalized(
    query: str,
    *,
    domain: str | None = None,
    vocabulary_id: str | None = None,
    standard_only: bool = False,
    limit: int = 20,
    allow_unindexed_scan: bool = False,
) -> list[ConceptMatch]
```

Normalized search: lowercases and strips punctuation from both the query and concept names before matching. Catches common surface-form differences (abbreviations, spacing, case) that exact search misses without the false-positive risk of full-text search.

#### Why this one needs a selective filter

Comparison is on a *computed* expression, so an ordinary index on `concept_name` cannot serve it. Without a selective filter the planner scans every concept. Measured against a 1.7M-concept CDM:

| Call | Time |
| --- | --- |
| unconstrained | 40s |
| `domain='Drug'` | 25s |
| `vocabulary_id='MedDRA'` | 0.4s |

So the call is **refused** unless one of the following holds:

- `vocabulary_id` or `parent_ids` is given. Note that `domain` does **not** count — it looks like a narrowing filter but the largest domains hold millions of concepts, which is why `domain='Drug'` still took 25s.
- `allow_unindexed_scan=True`, which accepts the scan deliberately.

It raises rather than warning because the unindexed query does not fail — it succeeds slowly, so a warning gets absorbed into a caller that merely looks sluggish.

### `search_fulltext`

```python
vocab.search_fulltext(
    query: str,
    *,
    domain: str | None = None,
    vocabulary_id: str | None = None,
    standard_only: bool = False,
    include_synonyms: bool = True,
    min_rank: float = 0.0,
    limit: int = 20,
) -> tuple[list[ConceptMatch], bool]
```

PostgreSQL full-text search against the `concept_name_tsvector` GIN-indexed sidecar column, with `ts_rank` scores included on each result. Returns a `(results, fts_available)` tuple. When `fts_available=False` (the sidecar column is absent), `results` is always `[]` — fall through to embedding search or exact search instead.

FTS sidecar detection is lazy and cached after the first call. No configuration is required: if the column is present it is used, and if it is absent `fts_available` is simply `False`.

Synonym FTS is included when the `concept_synonym_name_tsvector` sidecar is also present. If the synonym sidecar is absent, synonym results are silently omitted — it is not an error.

### `navigate_to_standard`

```python
vocab.navigate_to_standard(
    concept_ids: list[int],
) -> list[ConceptMappingResult]
```

Batch navigation: given a list of concept IDs (which may be non-standard), returns their standard OMOP equivalents by following `"Maps to"` relationship edges. Two queries total regardless of input list size.

Concepts that are already standard are returned as self-mappings (`relationship_id="self"`). Concepts with no `"Maps to"` edge have an empty `targets` list. Concept IDs not found in the vocabulary are silently omitted.

### `navigate_to_value`

```python
vocab.navigate_to_value(
    concept_ids: list[int],
) -> list[ConceptMappingResult]
```

Same batch navigation pattern as `navigate_to_standard`, but follows `"Maps to value"` relationship edges. Used for value-domain mapping in OMOP measurement and observation workflows. Results use the same `ConceptMappingResult` shape, with targets in `targets`.

### `navigate_to_unit`

```python
vocab.navigate_to_unit(
    concept_ids: list[int],
) -> list[ConceptMappingResult]
```

Batch navigation following `"Maps to unit"` relationship edges. Results use the same `ConceptMappingResult` shape, with targets in `targets`.

## Return types

### `ConceptMatch`

```python
@dataclass
class ConceptMatch:
    concept_id: int
    concept_name: str
    concept_code: str
    vocabulary_id: str
    domain_id: str
    concept_class_id: str
    standard_concept: bool
    invalid_reason: str | None
    match_source: Literal["name", "synonym"]
    matched_synonym: str | None
    ts_rank: float | None  # only set by search_fulltext
```

### `ConceptMappingResult`

```python
@dataclass
class ConceptMappingResult:
    source_concept_id: int
    source_concept_name: str
    source_standard_concept: bool
    source_classification_concept: bool
    targets: list[MappedConcept]
```

### `MappedConcept`

```python
@dataclass
class MappedConcept:
    concept: dict[str, Any]  # concept payload, including identity and quality fields
    relationship_id: str     # "Maps to", "Maps to value", "Maps to unit", or "self"
```

The direct Python API uses `targets` for all three navigation methods. MCP serialization names that list `standard_concepts` for `navigate_to_standard` and `related_concepts` for value and unit navigation.

## Serialization helpers

`VocabService` provides `serialise_*` helpers that convert the typed return objects to plain dicts suitable for MCP tool responses:

- `serialise_concept_match(match: ConceptMatch) -> dict`
- `serialise_standard_mapping(mapping: ConceptMappingResult) -> dict`
- `serialise_related_concept_mapping(mapping: ConceptMappingResult) -> dict`

The search MCP tools in `search_tools.py` use these helpers to convert service results into the wire format. Direct Python consumers can use the dataclasses directly without serializing.

## Error handling

- Raises `GroundworkersError` for database errors (connection failures, query errors).
  The `code` is always one of the documented error codes (`QUERY_ERROR`,
  `BACKEND_UNAVAIL`).
- Raises `ValueError` for invalid arguments (empty query string, negative limit).
- Never returns error dicts — that is the tool layer's responsibility.
