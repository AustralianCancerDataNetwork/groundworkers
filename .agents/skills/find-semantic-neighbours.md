# find-semantic-neighbours

Find OMOP concepts that are semantically similar to a query term or a known
concept, using the embedding index. Returns concepts ranked by vector
similarity rather than lexical overlap or graph distance.

## When to use

Use this skill when:
- Lexical search misses concepts because they use different terminology
  (e.g. "heart attack" vs. "myocardial infarction")
- You want to discover related concepts you may not have thought to search for
- You are exploring the semantic neighbourhood of a known concept to check
  for gaps in a concept set
- `concept_ground` returns no results or low-confidence results

Requires the embedding index to be configured and available. Call
`embedding_index_status` first if you are unsure.

## Instructions

### When you have a free-text query

1. Call `embedding_search` with `query` set to the term.
   - Pass `domain` to restrict results to a specific OMOP domain.
   - Set `standard_only: true` if you only want standard concepts.
   - Default `limit` of 10 is usually sufficient for exploration; increase
     to 20–30 for concept set building.

2. Results include `concept_id`, `concept_name`, `domain_id`,
   `vocabulary_id`, `score` (cosine similarity, higher is better).

3. Scores above ~0.85 are typically strong semantic matches. Scores between
   0.7 and 0.85 are related but may not be equivalent — verify with
   `explore-concept` before including in a concept set.

### When you have a known concept_id

1. Call `embedding_neighbours` with `concept_id`.
   - This returns concepts whose embeddings are closest to the seed concept
     in vector space — the nearest neighbours in semantic meaning.
   - Useful for discovering synonymous or closely related concepts that are
     not linked by explicit OMOP graph relationships.

2. Compare results against the seed concept's hierarchy (from
   `concept_ancestors` / `concept_descendants`) to identify semantically
   similar concepts that sit in a different part of the OMOP graph.

### Combining with lexical results

Embedding search complements, not replaces, lexical grounding. A robust
concept set exploration combines:
- `concept_ground` for precise lexical matches
- `find-semantic-neighbours` for semantically related terms that lexical
  search misses

Call `explore-concept` on any candidate from either source before including
it in a final concept set.

## Notes

- Embedding quality depends on the model used at index build time. Check
  `embedding_index_status` for the registered model name.
- If `embedding_index_status` shows no models registered or the index is
  unavailable, this skill cannot be used — fall back to `concept_ground`
  and `concept_candidate_bundle` with `include_embedding: false`.
