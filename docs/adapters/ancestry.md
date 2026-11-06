# Stored ancestry and shared targets

Groundworkers reads OMOP's `CONCEPT_ANCESTOR` closure table. Its `max_depth` is
the stored `min_levels_of_separation`, rather than a count of recursive
parent lookups. Distinct concepts can have separation zero, so zero depth can
include more than the input concept itself.

## Find a shared standard ancestor

Use `concept_shared_standard_ancestor` to find a shared representation for two
or more candidate concepts for the same source record. It does not combine
separate drug ingredients into a product or establish clinical equivalence.

```python
result = context.concept_shared_standard_ancestor(
    [first_concept_id, second_concept_id],
    max_depth=5,
    concept_class_ids=["Clinical Drug"],
    vocabulary_ids=["RxNorm", "RxNorm Extension"],
)
```

The Python/plugin method returns the service payload. The MCP operation wraps
that payload in its typed `data` field; errors have `error=true`, a code and a
message, with no selected target.

The operation requires 2–200 supplied positive integer IDs and at least two
distinct IDs. Boolean, string and fractional IDs are rejected. Class and
vocabulary filters use exact catalogue identifiers; null means unrestricted,
while empty lists, blank values and unknown identifiers are invalid. Asking
for RxNorm does not implicitly include RxNorm Extension.

Groundworkers intersects the complete stored ancestry of all inputs, including
each input at distance zero, then filters to active standard targets in the same
domain and any requested classes/vocabularies. It minimizes the maximum stored
distance from the inputs. A unique nearest target is selected; equal nearest
targets remain ambiguous. Unknown input IDs are errors, and mixed input domains
produce an incompatible-input result.

`limit` (1–50) and `offset` paginate displayed common candidates. They do not
affect selection: displaying one candidate cannot resolve a tie. The response
reports selection completeness, total candidates and display truncation
separately. A database failure returns an error rather than a partial selection.

## Nearest ancestors and path evidence

`concept_nearest_standard_ancestor` uses the same stored-distance semantics and
reports ambiguous nearest targets. Its text-query form discloses the rank-one
grounded seed and other seed candidates; it does not establish that the source
concept itself was unambiguous.

Relationship paths are separate evidence from stored ancestry. An ancestor can
be valid even when a bounded relationship-path search finds no path. The result
reports that limitation rather than fabricating a path. A `Maps to` fallback is
labelled as mapping evidence, not ancestry.

## Bounded named-relationship neighborhoods

The Python/plugin `concept_relationship_neighborhood` capability follows an
explicit set of active outbound relationship IDs. It accepts up to 50 distinct
seed concepts, traverses at most three levels and bounds each seed's nodes and
edges independently. `max_nodes_per_seed` and `max_edges_per_seed` describe
those allowances; `returned_edge_count` counts the deduplicated edges actually
returned across all seeds. One dense seed does not consume another seed's
allowance. `truncated_seeds` identifies walks that reached a resource limit.

The maximum supported union before overlap deduplication is 25,000 nodes and
125,000 edges (50 seeds, 500 nodes and 2,500 edges per seed). Prefer small seed
sets and limits for interactive use. Unknown seed concepts return `NOT_FOUND`
with the missing seed IDs. Missing metadata for reached non-seed concepts returns
`QUERY_ERROR` rather than a silently incomplete neighborhood.
