# explore-concept

Retrieve hierarchy, relationships, and mapping context for a known OMOP
concept_id. Use this to evaluate whether a candidate concept is the right
choice before committing to a mapping.

## When to use

Use this skill after `map-clinical-term` or `map-source-code` returns
candidates, when you want to:

- Check whether the concept is at the right level of specificity (e.g. a
  broad "Diabetes mellitus" vs. the more specific "Type 2 diabetes mellitus")
- See what the concept's parent and sibling concepts are
- Confirm it is a standard concept in the correct domain
- Understand what other concepts it is related to via OMOP relationships

## Instructions

1. Call `concept_mapping_context` with the `concept_id` to evaluate.
   Default settings return ancestors, standard mapping, relationship
   summary, and neighbours — this is sufficient for most mapping decisions.

2. Review the response:
   - `concept` — the concept record itself (name, domain, vocabulary,
     standard_concept flag)
   - `standard_mapping` — if the concept is non-standard, shows where it
     maps to; if it is already standard, confirms this
   - `ancestors` — parent chain up the hierarchy; use these to judge
     whether the concept is appropriately specific
   - `relationship_summary` — counts of inbound/outbound relationship types
   - `neighbors` — directly related concepts (attributes, morphologies,
     associated procedures, etc.)

3. If the ancestors show the concept sits at too broad a level (e.g. you
   wanted a specific drug but got a drug class), go back to
   `map-clinical-term` with a more specific query, or use
   `concept_descendants` to explore children of the current concept.

4. If `standard_concept` is `null` or `C` is not in the response, the
   concept is non-standard. Check `standard_mapping` for the recommended
   standard concept_id to use instead.
