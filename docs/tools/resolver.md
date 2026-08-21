# Resolver Tools

Resolver tools turn free text into ranked OMOP concept candidates. They are backed by `ConceptGroundingService` over the omop-graph backend. A result can be a standard concept, a non-standard source concept, or a classification concept; inspect the returned flags before treating it as a CDM mapping target.

If you already have a known identifier, use [Concept Tools](concept.md) instead. If you want raw lexical search primitives without the tiered resolver policy, use [Search Tools](search.md).

## `concept_ground`

Full-featured free-text grounding for clinical terms and source labels.

```json
{
  "query": "lung cancer",
  "limit": 5,
  "domain": "Condition",
  "vocabulary_id": "SNOMED",
  "parent_ids": [441840]
}
```

All parameters except `query` are optional. `limit` is clamped to `1..20`.

`parent_ids` is an optional ancestry constraint. When provided, returned concepts must be descendants of at least one listed concept. When omitted, the grounding search runs without an ancestry constraint.

**Response**:

```json
{
  "query": "lung cancer",
  "results": [
    {
      "concept_id": 4119419,
      "concept_name": "Malignant neoplasm of bronchus and lung",
      "vocabulary_id": "SNOMED",
      "domain_id": "Condition",
      "concept_class_id": "Clinical Finding",
      "standard_concept": true,
      "classification_concept": false,
      "total_score": 1.0,
      "match_kind": "EXACT"
    }
  ],
  "grounding_explanation": {
    "matched_tier": "EXACT",
    "used_embedding": false,
    "effective_parent_ids": [441840],
    "parent_ids_source": "explicit"
  }
}
```

`match_kind` indicates which resolver tier produced the result:

| Value | Meaning |
|---|---|
| `EXACT` | Case-insensitive exact match on concept name or synonym |
| `FULLTEXT` | PostgreSQL full-text search |
| `EMBEDDING_NEAREST` | Nearest-neighbour embedding search |
| `PARTIAL` | `ILIKE` fragment match used as the last lexical fallback |

The resolver short-circuits on the first tier that returns any result.

Use `grounding_explanation` when you need to understand how constrained or strong the result is:

- `matched_tier` tells you which resolver family produced the winning candidates
- `used_embedding` tells you whether embedding scoring participated
- `effective_parent_ids` shows the ancestry constraint actually applied
- `parent_ids_source` is `explicit` when the caller supplied `parent_ids`, else `none`

For finer control over retrieval strategy, inspect the lower-level lexical and embedding tools directly.
