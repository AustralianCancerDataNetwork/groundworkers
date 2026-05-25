# System Tools

Two tools report on the overall health and vocabulary catalogue of the groundworkers
instance.  Both are **always registered** regardless of adapter configuration.

## `system_status`

Returns the availability of each configured adapter.  Takes no arguments.

**Response**:
```json
{
  "available": true,
  "adapters": {
    "omop_graph": {"available": true},
    "omop_emb": {
      "available": true,
      "models": [{"model_name": "qwen3-embedding:0.6b", "concept_count": 438924}]
    },
    "oa_cohorts": {"available": false, "reason": "not configured"}
  }
}
```

`"available"` at the top level is `true` if **any** adapter is available.  Each
adapter reports its own availability independently.  Unconfigured adapters appear
with `"available": false` and a `"reason"` string.

!!! tip "Health check"
    groundcrew calls `system_status` at session start to verify the groundworkers
    instance is reachable and has the expected adapters available.

## `system_vocabulary_catalogue`

Returns all OMOP vocabularies, domains, and concept classes from the vocabulary
database.  Requires `omop_graph` to be configured; returns `BACKEND_UNAVAIL` otherwise.

**Response**:
```json
{
  "vocabularies": [
    {"vocabulary_id": "SNOMED", "vocabulary_name": "SNOMED CT", "concept_count": 423891}
  ],
  "domains": [
    {"domain_id": "Condition", "domain_name": "Condition", "concept_count": 153240}
  ],
  "concept_classes": [
    {"concept_class_id": "Clinical Finding", "concept_class_name": "Clinical Finding"}
  ]
}
```

This response is typically cached by groundcrew at Stage 0 of the grounding
pipeline to avoid repeated round-trips during a mapping session.
