"""Usage instructions selected from the final registered MCP tool surface."""

from collections.abc import Collection

INSTRUCTION_SECTIONS: tuple[tuple[str, frozenset[str], str], ...] = (
    (
        "intro",
        frozenset({"system_status"}),
        (
            "Groundworkers gives deterministic access to an OMOP vocabulary: search, "
            "grounding, the concept graph and convention packs. system_status shows what "
            "is available."
        ),
    ),
    (
        "knowledge",
        frozenset({"knowledge_catalogue", "knowledge_pack"}),
        (
            "Before mapping, load conventions once per job: knowledge_catalogue with the "
            "domains involved, then knowledge_pack for each pack that applies. Pack rules"
            " override habit."
        ),
    ),
    (
        "mapping",
        frozenset(
            {
                "concept_candidate_bundle",
                "concept_mapping_context",
                "concept_descendants",
                "concept_nearest_standard_ancestor",
            }
        ),
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


def build_instructions(
    registered_tools: Collection[str],
) -> tuple[str | None, tuple[str, ...]]:
    """Return applicable section text and names in their declared order."""
    registered = frozenset(registered_tools)
    included = [
        (name, text)
        for name, tools, text in INSTRUCTION_SECTIONS
        if tools <= registered
    ]
    if not included:
        return None, ()
    return "\n\n".join(text for _, text in included) + "\n", tuple(
        name for name, _ in included
    )
