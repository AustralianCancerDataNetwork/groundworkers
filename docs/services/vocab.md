# VocabService

`VocabService` provides vocabulary search and concept navigation over OMOP CDM
vocabulary tables. It is the direct Python API for lexical retrieval operations —
the same operations exposed via the search MCP tools.

## Construction

`VocabService` takes a single `CDMAdapter` argument. It uses the adapter's session
factory to open per-call database sessions.

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.load("config/groundworkers.local.yaml")
app = build_application(config)

vocab = app.services.vocab
```

`vocab` is `None` when `omop_graph` is not configured.

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

Case-insensitive exact match of `query` against `concept_name` and (when
`include_synonyms=True`) `concept_synonym_name`. Name matches are returned before
synonym matches. Concepts that match both their name and a synonym appear only once,
under `"name"`.

`standard_only` defaults to `False`. Non-standard concepts often have better lexical
coverage (trade names, source codes, regional synonyms) than their standard equivalents;
filtering them out in the search step loses those signals. Use `navigate_to_standard`
afterward to resolve non-standard results.

### `search_normalized`

```python
vocab.search_normalized(
    query: str,
    *,
    domain: str | None = None,
    vocabulary_id: str | None = None,
    standard_only: bool = False,
    limit: int = 20,
) -> list[ConceptMatch]
```

Normalized search: lowercases and strips punctuation from both the query and concept
names before matching. Catches common surface-form differences (abbreviations, spacing,
case) that exact search misses without the false-positive risk of full-text search.

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

PostgreSQL full-text search against the `concept_name_tsvector` GIN-indexed sidecar
column, with `ts_rank` scores included on each result. Returns a `(results, fts_available)`
tuple. When `fts_available=False` (the sidecar column is absent), `results` is always
`[]` — fall through to embedding search or exact search instead.

FTS sidecar detection is lazy and cached after the first call. No configuration is
required: if the column is present it is used, and if it is absent `fts_available`
is simply `False`.

Synonym FTS is included when the `concept_synonym_name_tsvector` sidecar is also
present. If the synonym sidecar is absent, synonym results are silently omitted — it
is not an error.

### `navigate_to_standard`

```python
vocab.navigate_to_standard(
    concept_ids: list[int],
) -> list[StandardMapping]
```

Batch navigation: given a list of concept IDs (which may be non-standard), returns
their standard OMOP equivalents by following `"Maps to"` relationship edges. Two
queries total regardless of input list size.

Concepts that are already standard are returned as self-mappings
(`relationship_id="self"`). Concepts with no `"Maps to"` edge return an empty
`standard_concepts` list. Concept IDs not found in the vocabulary are silently omitted.

### `navigate_to_value`

```python
vocab.navigate_to_value(
    concept_ids: list[int],
) -> list[RelatedConceptMapping]
```

Same batch navigation pattern as `navigate_to_standard`, but follows `"Maps to value"`
relationship edges. Used for value-domain mapping in OMOP measurement and observation
workflows.

### `navigate_to_unit`

```python
vocab.navigate_to_unit(
    concept_ids: list[int],
) -> list[RelatedConceptMapping]
```

Batch navigation following `"Maps to unit"` relationship edges.

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

### `StandardMapping`

```python
@dataclass
class StandardMapping:
    source_concept_id: int
    source_concept_name: str
    source_standard_concept: bool
    standard_concepts: list[MappedConcept]
```

### `MappedConcept`

```python
@dataclass
class MappedConcept:
    concept_id: int
    concept_name: str
    vocabulary_id: str
    domain_id: str
    concept_class_id: str
    relationship_id: str  # "Maps to", "Maps to value", "Maps to unit", or "self"
```

## Serialization helpers

`VocabService` provides `serialise_*` helpers that convert the typed return objects
to plain dicts suitable for MCP tool responses:

- `serialise_concept_match(match: ConceptMatch) -> dict`
- `serialise_standard_mapping(mapping: StandardMapping) -> dict`

The search MCP tools in `search_tools.py` use these helpers to convert service results
into the wire format. Direct Python consumers can use the dataclasses directly without
serializing.

## Error handling

- Raises `GroundworkersError` for database errors (connection failures, query errors).
  The `code` is always one of the documented error codes (`QUERY_ERROR`,
  `BACKEND_UNAVAIL`).
- Raises `ValueError` for invalid arguments (empty query string, negative limit).
- Never returns error dicts — that is the tool layer's responsibility.
