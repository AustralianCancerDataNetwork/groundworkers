# map-data-dictionary-row

Map a single data dictionary entry to a standard OMOP concept using a
cascade of strategies, from most to least precise. Returns the best mapping
found along with the strategy that succeeded.

This is the atomic operation that groundcrew applies across a full CSV
data dictionary. Use it to map individual rows or to understand what
groundcrew does at each stage.

## When to use

Use this skill when you have a data dictionary row — typically a column
label, an optional source code, and an optional domain hint — and want the
best available standard OMOP mapping.

## Strategy cascade

Work through these stages in order, stopping at the first that returns a
confident result.

### Stage 1 — Source code mapping (if code is available)

If `vocabulary_id` and `concept_code` are both provided:

1. Call `concept_map_to_standard` with those values.
2. If `standard_concepts` is non-empty, this is the mapping. Record
   `strategy: "source_code"` and proceed to verification (below).
3. If the result is `NOT_FOUND` or `standard_concepts` is empty, fall
   through to Stage 2.

### Stage 2 — Free-text grounding

1. Call `concept_ground` with `label` as `query` and optional `domain`.
2. If the top result has `match_kind` of `EXACT` or `FULLTEXT` and
   `total_score ≥ 0.7`, accept it. Record `strategy: "ground_exact"` or
   `"ground_fulltext"` accordingly.
3. If `match_kind` is `EMBEDDING_NEAREST` and `total_score ≥ 0.5`,
   treat as a tentative match and continue to verification.
4. If no results or low confidence, fall through to Stage 3.

### Stage 3 — Candidate bundle

1. Call `concept_candidate_bundle` with `query` set to `label`, optional
   `domain`, and `include_embedding: true`.
2. Review the merged candidate list. If any candidate has a high composite
   score across multiple channels (normalized + fulltext + embedding), accept
   it. Record `strategy: "candidate_bundle"`.
3. If still no confident match, fall through to Stage 4.

### Stage 4 — Parent backoff

1. Call `concept_parent_backoff` with `query` set to `label`.
2. This climbs the hierarchy to find the nearest standard ancestor when the
   specific term cannot be grounded directly.
3. Accept the result but flag it as `strategy: "parent_backoff"` and note
   that the mapping is at a broader level than the original term — it may
   require human review.

### No match

If all four stages return no result, record `strategy: "unmatched"` and
return the label for manual review.

## Verification step

After any successful stage, call `concept_mapping_context` on the matched
`concept_id` to confirm:
- `standard_concept = 'S'` (it is a standard concept)
- The `domain_id` matches the expected domain for this row
- The `ancestors` confirm the concept is at a sensible level of specificity

## Output to report per row

- `label` — original data dictionary label
- `concept_id` — matched standard concept_id (or null)
- `concept_name` — matched concept name
- `domain_id`, `vocabulary_id`
- `strategy` — which stage produced the result
- `confidence` — qualitative: high / medium / low / unmatched
- `needs_review` — true when strategy is `parent_backoff` or confidence is low
