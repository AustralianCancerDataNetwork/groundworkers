# map-clinical-term

Map a free-text clinical label or data dictionary entry to ranked standard
OMOP concepts.

## When to use

Use this skill when you have a human-readable term — a column label, a
diagnosis description, a procedure name, a drug name — and need to find the
best matching standard OMOP concept_id.

## Instructions

1. Call `concept_ground` with the term as `query`.
   - Optionally pass `domain` (e.g. `Condition`, `Drug`, `Measurement`,
     `Procedure`, `Observation`) to restrict results.
   - Leave `limit` at the default (5) unless you need more candidates to
     choose from.

2. Inspect the results. Each result includes:
   - `concept_id`, `concept_name`, `domain_id`, `vocabulary_id`
   - `match_kind` — which resolver tier matched:
     - `EXACT` — case-insensitive exact match on name or synonym
     - `FULLTEXT` — PostgreSQL full-text search
     - `EMBEDDING_NEAREST` — nearest-neighbour semantic search
     - `PARTIAL` — last-resort fragment match
   - `total_score` — composite relevance score (higher is better)
   - `standardized_from` — present when the result was mapped up from a
     non-standard source concept

3. If the top result looks plausible, confirm it by calling `explore-concept`
   on its `concept_id` to inspect ancestors and check the level of specificity
   is appropriate for your use case.

4. If no results are returned, try broadening the query (remove qualifiers),
   or use a different `domain`.

## Interpreting confidence

- `EXACT` or `FULLTEXT` matches at `total_score` ≥ 0.8 are usually reliable.
- `EMBEDDING_NEAREST` matches warrant a quick `explore-concept` check.
- `PARTIAL` matches should always be verified.
- When `match_kind` is absent or `grounding_explanation` says embedding
  scoring was inactive, the result is lexical only.
