# Concept Tools

Eleven tools provide read-only access to OMOP concepts, hierarchy, relationships,
standard mappings, and free-text grounding.  All require `omop_graph` to be configured.

For agent-composable search primitives (exact match, full-text, standard navigation)
see [Search Tools](search.md).

---

## `concept_get`

Returns a single OMOP concept by its integer `concept_id`.

```json
{"concept_id": 4119419}
```

**Response** (success):
```json
{
  "concept_id": 4119419,
  "concept_name": "Malignant neoplasm of bronchus and lung",
  "concept_code": "363358000",
  "vocabulary_id": "SNOMED",
  "domain_id": "Condition",
  "concept_class_id": "Clinical Finding",
  "standard_concept": true,
  "valid_start_date": "2002-01-31",
  "valid_end_date": "2099-12-31",
  "invalid_reason": null
}
```

`standard_concept` is a boolean (`true`/`false`).

Returns `NOT_FOUND` if the concept ID does not exist.

---

## `concept_by_code`

Returns concepts matching a vocabulary ID and concept code pair.

```json
{"vocabulary_id": "SNOMED", "concept_code": "363358000"}
```

**Response**: `{"concepts": [...]}` — array of concept dicts in the same shape as `concept_get`.

---

## `concept_ground`

Full-featured free-text grounding — the primary entry point for mapping a text string
or clinical term to an OMOP standard concept.

```json
{
  "query": "lung cancer",
  "limit": 5,
  "domain": "Condition",
  "vocabulary_id": "SNOMED"
}
```

All parameters except `query` are optional.  `limit` is clamped to `[1, 20]`.

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
      "total_score": 1.0,
      "match_kind": "EXACT"
    }
  ]
}
```

`match_kind` indicates which resolver tier produced the result:

| Value | Meaning |
|---|---|
| `EXACT` | Case-insensitive exact match on concept_name or synonym |
| `FULLTEXT` | PostgreSQL full-text search (requires tsvector sidecar column) |
| `EMBEDDING_NEAREST` | Nearest-neighbour embedding search |
| `PARTIAL` | `ILIKE` fragment match (last resort) |

The pipeline short-circuits on the first tier that returns any result.

For finer control over resolver selection and quality thresholds, use
`concept_search_exact`, `concept_search_fulltext`, `embedding_search`, and
`concept_navigate_to_standard` directly.

---

## `concept_ancestors`

Returns ancestors of a concept up to `max_depth` hops (default 5, max 20).

```json
{"concept_id": 4119419, "max_depth": 3}
```

**Response**:
```json
{
  "concept_id": 4119419,
  "ancestors": [
    {"concept_id": 441484, "concept_name": "Neoplasm", "vocabulary_id": "SNOMED", "domain_id": "Condition", "standard_concept": true, "depth": 1},
    {"concept_id": 4008453, "concept_name": "Disease", "vocabulary_id": "SNOMED", "domain_id": "Condition", "standard_concept": true, "depth": 2}
  ]
}
```

---

## `concept_descendants`

Returns descendants of a concept up to `max_depth` hops (default 3, max 10).

```json
{"concept_id": 441484, "max_depth": 2}
```

**Response**: same shape as ancestors, with `"descendants"` key instead of `"ancestors"`.

---

## `concept_relationships`

Returns all direct relationships (edges) for one concept, grouped by direction.

```json
{"concept_id": 4119419}
```

**Response**:
```json
{
  "concept_id": 4119419,
  "outbound": [
    {
      "relationship_id": "Is a",
      "predicate_kind": "HIERARCHY",
      "target_concept_id": 441484,
      "target_concept_name": "Neoplasm",
      "valid": true
    }
  ],
  "inbound": [
    {
      "relationship_id": "Subsumes",
      "predicate_kind": "HIERARCHY",
      "source_concept_id": 4248978,
      "source_concept_name": "Adenocarcinoma of lung",
      "valid": true
    }
  ]
}
```

`predicate_kind` is one of `HIERARCHY`, `IDENTITY`, `ATTRIBUTE`, `COMPOSITION`, `ASSOCIATION`.

---

## `concept_equivalency_path`

Returns the shortest path between two concepts traversing only identity (and optionally
hierarchy) relationships.  Use this to find cross-vocabulary equivalences — for example,
confirming that an ICD-10 code maps to the same SNOMED concept as a OMOP standard concept.

```json
{
  "source_id": 45542442,
  "target_id": 4119419,
  "allow_hierarchical_traversal": false,
  "max_depth": 8
}
```

All parameters except `source_id` and `target_id` are optional.

`allow_hierarchical_traversal=false` (default): only IDENTITY predicates (`Maps to`,
`Concept same_as`, `Concept poss_eq`, etc.).  The connection found represents a direct
cross-vocabulary equivalence with no loss of specificity.

`allow_hierarchical_traversal=true`: also allows HIERARCHY predicates (`Is a` /
`Subsumes`).  A path may then step up or down the ancestry chain; the connection may
be at a broader level of abstraction.

**Response** (same shape as `concept_path`):
```json
{
  "source_id": 45542442,
  "target_id": 4119419,
  "found": true,
  "paths": [
    {
      "length": 1,
      "steps": [
        {
          "subject_id": 45542442,
          "subject_name": "Malignant neoplasm of bronchus and lung",
          "predicate": "Maps to",
          "predicate_kind": "IDENTITY",
          "object_id": 4119419,
          "object_name": "Malignant neoplasm of bronchus and lung"
        }
      ]
    }
  ]
}
```

Returns `{"found": false, "paths": []}` when no path exists within `max_depth`.

---

## `concept_path`

Finds the shortest path between two concepts across **all** relationship types
(HIERARCHY, IDENTITY, ATTRIBUTE, COMPOSITION, ASSOCIATION).

```json
{
  "source_id": 4119419,
  "target_id": 441484,
  "max_depth": 8,
  "within_domain": true
}
```

`within_domain=true` (default) restricts traversal to edges where both endpoints share
the same `domain_id`.  Set `false` to allow cross-domain traversal via attribute
relationships.

**Response**:
```json
{
  "source_id": 4119419,
  "target_id": 441484,
  "found": true,
  "paths": [
    {
      "length": 1,
      "steps": [
        {
          "subject_id": 4119419,
          "subject_name": "Malignant neoplasm of bronchus and lung",
          "predicate": "Is a",
          "predicate_kind": "HIERARCHY",
          "object_id": 441484,
          "object_name": "Neoplasm"
        }
      ]
    }
  ]
}
```

Returns `{"found": false, "paths": []}` when no path exists within `max_depth`.
When `source_id == target_id`, returns `{"found": true, "paths": [{"length": 0, "steps": []}]}`.

For cross-vocabulary equivalence queries, prefer `concept_equivalency_path` which
restricts traversal to identity edges only.

---

## `concept_map_to_standard`

Maps a non-standard concept (identified by vocabulary + code) to its standard OMOP
equivalent via IDENTITY-type relationship edges.

```json
{"vocabulary_id": "ICD10CM", "concept_code": "C34.1"}
```

**Response**:
```json
{
  "source": {"concept_id": 45542442, "vocabulary_id": "ICD10CM", "standard_concept": false, ...},
  "standard_concepts": [
    {"concept_id": 4119419, "vocabulary_id": "SNOMED", "standard_concept": true, ...}
  ]
}
```

`standard_concepts` is an empty list if no standard mapping exists.  When the source
concept is already standard, it is returned as-is in `standard_concepts`.
