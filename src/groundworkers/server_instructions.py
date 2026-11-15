"""Usage instructions selected from the final registered MCP tool surface."""

from collections.abc import Collection
from string import punctuation

# Keep the catalogue independent of configured backends. Tests compare it with
# every built-in registration so tool renames cannot silently weaken selection.
TOOL_CATALOGUE = frozenset(
    {
        "concept_ancestors",
        "concept_associations",
        "concept_by_code",
        "concept_candidate_bundle",
        "concept_descendants",
        "concept_equivalency_path",
        "concept_extended_inheritance",
        "concept_get",
        "concept_ground",
        "concept_map_to_standard",
        "concept_map_to_value",
        "concept_mapping_context",
        "concept_navigate_to_standard",
        "concept_nearest_standard_ancestor",
        "concept_neighbors",
        "concept_path",
        "concept_relationships",
        "concept_resolve_mapping_expression",
        "concept_search_exact",
        "concept_search_fulltext",
        "concept_search_normalized",
        "concept_shared_standard_ancestor",
        "domain_classify",
        "embedding_encode",
        "embedding_index_status",
        "embedding_neighbours",
        "embedding_search",
        "embedding_search_batch",
        "knowledge_catalogue",
        "knowledge_pack",
        "mapping_evaluate_candidates",
        "semantic_project",
        "source_plan",
        "source_plan_assisted",
        "system_status",
        "system_vocabulary_catalogue",
        "text_decompose",
        "text_disambiguate",
        "text_mapping_cleanup",
        "text_normalize",
    }
)

INSTRUCTION_SECTIONS: tuple[tuple[str, str], ...] = (
    (
        "intro",
        (
            "Groundworkers gives deterministic access to an OMOP vocabulary: search, "
            "grounding, the concept graph and convention packs. system_status shows what "
            "is available."
        ),
    ),
    (
        "knowledge",
        (
            "Before mapping, load conventions once per job: knowledge_catalogue with the "
            "domains involved, then knowledge_pack for each pack that applies. Pack rules"
            " override habit."
        ),
    ),
    (
        "mapping",
        (
            "To map a source term:\n"
            "1. Note every qualifier it states (specimen, site, laterality, route, form, "
            "unit, method).\n"
            "2. Gather candidates with concept_candidate_bundle; keep standard concepts "
            "in the right domain.\n"
            "3. Check the hierarchy before deciding (concept_mapping_context, "
            "concept_descendants): pick the most detailed standard concept the source "
            "supports. A child that keeps a stated qualifier beats its parent; never add "
            "detail the source lacks.\n"
            "4. Vocabulary is not a preference by itself; packs decide between equally "
            "precise concepts.\n"
            "5. No fit: concept_nearest_standard_ancestor, or say no standard concept "
            "fits."
        ),
    ),
)
_WORD_SEPARATORS = str.maketrans({char: " " for char in punctuation if char != "_"})


def instruction_requirements(text: str) -> frozenset[str]:
    """Match whole tool names from the catalogue, leaving field names alone."""
    words = text.translate(_WORD_SEPARATORS).split()
    return TOOL_CATALOGUE.intersection(words)


def build_instructions(
    registered_tools: Collection[str],
) -> tuple[str | None, tuple[str, ...]]:
    """Return applicable section text and names in their declared order."""
    registered = frozenset(registered_tools)
    included = [
        (name, text)
        for name, text in INSTRUCTION_SECTIONS
        if instruction_requirements(text) <= registered
    ]
    if not included:
        return None, ()
    return "\n\n".join(text for _, text in included) + "\n", tuple(
        name for name, _ in included
    )
