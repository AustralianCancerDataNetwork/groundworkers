# Search Tools

These tools expose lexical concept search with its retrieval signals. They are
backed by `VocabService` and require shared OMOP vocabulary access.

Unlike `concept_ground`, which runs a tiered pipeline and stops at the first tier
with results, these tools let the client choose a strategy, inspect its signals,
and handle non-standard candidates explicitly.

---

## `concept_search_exact`

Case-insensitive exact match of a query string against `concept_name` and (optionally)
`concept_synonym_name`.

```json
{
  "query": "lung cancer",
  "domain": "Condition",
  "vocabulary_id": null,
  "standard_only": false,
  "include_synonyms": true,
  "limit": 20
}
```

All parameters except `query` are optional.  `limit` is clamped to `[1, 50]`.

`standard_only` defaults to `false` — results include non-standard concepts so the
caller can inspect `standard_concept` and decide whether to call
`concept_navigate_to_standard`.

**Response**:
```json
{
  "query": "lung cancer",
  "results": [
    {
      "concept_id": 4119419,
      "concept_name": "Malignant neoplasm of bronchus and lung",
      "concept_code": "363358000",
      "vocabulary_id": "SNOMED",
      "domain_id": "Condition",
      "concept_class_id": "Clinical Finding",
      "standard_concept": true,
      "invalid_reason": null,
      "match_source": "name",
      "matched_synonym": null
    }
  ]
}
```

`match_source` is `"name"` when `concept_name` matched, or `"synonym"` when a
`concept_synonym_name` matched.  `matched_synonym` contains the synonym string when
`match_source` is `"synonym"`, otherwise `null`.

Name matches are returned before synonym matches.  Concepts are de-duplicated — a
concept that matches both its name and a synonym appears only once (under `"name"`).

---

## `concept_search_fulltext`

PostgreSQL full-text search against the `concept_name_tsvector` GIN-indexed sidecar
column, with `ts_rank` scores exposed.

```json
{
  "query": "malignant bronchus lung",
  "domain": "Condition",
  "vocabulary_id": null,
  "standard_only": false,
  "include_synonyms": true,
  "min_rank": 0.0,
  "limit": 20
}
```

All parameters except `query` are optional.  `min_rank` must be in `[0.0, 1.0]` and
is applied server-side before returning results (useful to avoid very large result
sets); set to `0.0` to disable.

`tsvector_available` in the response indicates whether the sidecar column was detected.
When `false`, `results` is always `[]` and the caller should fall through to
`embedding_search` or `concept_search_exact`.

**Response**:
```json
{
  "query": "malignant bronchus lung",
  "tsvector_available": true,
  "results": [
    {
      "concept_id": 4119419,
      "concept_name": "Malignant neoplasm of bronchus and lung",
      "concept_code": "363358000",
      "vocabulary_id": "SNOMED",
      "domain_id": "Condition",
      "concept_class_id": "Clinical Finding",
      "standard_concept": true,
      "invalid_reason": null,
      "match_source": "name",
      "matched_synonym": null,
      "ts_rank": 0.075991
    }
  ]
}
```

Results are sorted by `ts_rank` descending.  The `ts_rank` field is only present in
results from this tool (not `concept_search_exact`).

Synonym FTS is included when the `concept_synonym_name_tsvector` sidecar is also
present.  If the synonym sidecar is absent, synonym results are silently omitted —
it is not an error.

!!! note "FTS sidecar detection is automatic"
    The tsvector sidecar columns are detected by inspecting the database schema on
    first use.  No configuration is needed — if they are present they will be used.

---

## `concept_navigate_to_standard`

Given a list of `concept_id` values, returns their standard OMOP equivalents by
following `"Maps to"` relationship edges.  Batch version of `concept_map_to_standard`,
intended to be called after `concept_search_exact`, `concept_search_fulltext`, or
`embedding_search` to resolve non-standard candidates in one round-trip.

```json
{"concept_ids": [45542442, 4119419]}
```

At most 100 concept IDs per call.

**Response**:
```json
{
  "results": [
    {
      "source_concept_id": 45542442,
      "source_concept_name": "Malignant neoplasm of bronchus and lung",
      "source_standard_concept": false,
      "standard_concepts": [
        {
          "concept_id": 4119419,
          "concept_name": "Malignant neoplasm of bronchus and lung",
          "vocabulary_id": "SNOMED",
          "domain_id": "Condition",
          "concept_class_id": "Clinical Finding",
          "relationship_id": "Maps to"
        }
      ]
    },
    {
      "source_concept_id": 4119419,
      "source_concept_name": "Malignant neoplasm of bronchus and lung",
      "source_standard_concept": true,
      "standard_concepts": [
        {
          "concept_id": 4119419,
          "concept_name": "Malignant neoplasm of bronchus and lung",
          "vocabulary_id": "SNOMED",
          "domain_id": "Condition",
          "concept_class_id": "Clinical Finding",
          "relationship_id": "self"
        }
      ]
    }
  ]
}
```

For concepts that are already standard: `standard_concepts` contains the concept
itself with `relationship_id = "self"`.  For concepts with no `"Maps to"` edge:
`standard_concepts` is `[]`.  Concept IDs not found in the vocabulary are silently
omitted from results.
