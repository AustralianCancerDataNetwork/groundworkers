# build-concept-set

Build an OMOP concept set from a clinical anchor term — ground the anchor,
confirm its position in the hierarchy, and expand to all descendant concepts.
The result is a flat list of standard concept_ids ready for use in ATLAS or
an OHDSI cohort definition.

## When to use

Use this skill when you need a complete concept set for a clinical entity —
a condition, drug class, procedure group, or measurement type — and want the
expansion to include all more-specific sub-types captured in the OMOP
hierarchy.

## Instructions

1. Call `concept_ground` with `query` set to the anchor term.
   - Pass `domain` if you know the expected OMOP domain (recommended).
   - Use `limit=1` unless you are unsure which concept is the right anchor.
   - Check `match_kind` — prefer `EXACT` or `FULLTEXT` anchors over
     `EMBEDDING_NEAREST` or `PARTIAL` without a manual review step.

2. Call `concept_mapping_context` on the top result's `concept_id`.
   - Inspect `ancestors` to confirm the concept sits at the right level
     of the hierarchy for your use case.
   - If the concept is too broad (e.g. "Neoplastic disease" when you wanted
     "Lung cancer"), call `concept_ground` again with a more specific query,
     or call `concept_descendants` on the broad concept and pick a more
     specific child as the new anchor.
   - If `standard_concept` is not `S`, use the `standard_mapping` entry
     to get the correct standard concept_id before expanding.

3. Call `concept_descendants` on the confirmed anchor `concept_id`.
   - `max_depth` controls how far down the hierarchy to expand. Start with
     3–5; increase for broad drug classes or condition hierarchies.
   - The response includes each descendant's `concept_id`, `concept_name`,
     `vocabulary_id`, `domain_id`, and `min_levels_of_separation`.

4. Assemble the concept set:
   - Include the anchor concept_id itself.
   - Include all standard descendants (`standard_concept = 'S'`).
   - Optionally filter out classification concepts (`standard_concept = 'C'`).
   - Report the anchor, total concept count, and `max_depth` used so the
     caller can reproduce or adjust the expansion later.

## Caveats

- Very broad anchors (e.g. "Neoplasm") can return thousands of descendants.
  Use a conservative `max_depth` and review before using in a study.
- Drug ingredient expansions may include clinical drug forms, branded drugs,
  and combination products. Consider filtering by `concept_class_id` if you
  only want ingredients or clinical drugs.
