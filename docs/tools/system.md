# System Tools

Two tools report on overall availability and the OMOP vocabulary catalogue for a
`groundworkers` deployment. Both are always registered.

## `system_status`

Returns the availability of each configured adapter. Takes no arguments.

**Response**:

```json
{
  "available": true,
  "adapters": {
    "omop_graph": {"available": true},
    "omop_emb": {
      "available": true,
      "models": [{"model_name": "qwen3-embedding:0.6b", "concept_count": 438924}]
    }
  }
}
```

Top-level `"available"` is `true` if at least one adapter is available. Each
adapter reports its own status independently.

Use this tool when you want to confirm:

- whether vocabulary lookup is available
- whether embedding search is available
- which embedding models are registered

## `system_vocabulary_catalogue`

Returns vocabularies, domains, and concept classes from the OMOP vocabulary
database.

This requires `omop_graph` to be configured and returns `BACKEND_UNAVAIL`
otherwise.

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

Clients often cache this response so they can present vocabulary and domain choices
without re-querying the server repeatedly.
