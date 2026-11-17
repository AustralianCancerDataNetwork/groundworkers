"""MCP schema guards and committed agent-surface fixtures.

The snapshot and budget are committed test fixtures. An intended surface change
needs ``--write``; raising a ceiling also needs a dated budget-history entry.
"""

from __future__ import annotations

import pytest

from tests.support.agent_surface import (
    BUDGET_PATH,
    REFERENCE_PATH,
    REGENERATE_COMMAND,
    budget_violation,
    diff_surfaces,
    load_json,
    reference_surface,
    surface_document,
    unbounded_numeric_params,
)

# Each remaining unbounded numeric parameter is intentional and reviewed here.
# Identifier values have no meaningful numeric range; gaps need bounded schemas.
UNBOUNDED_NUMERIC_PARAM_ALLOWLIST = {
    ("concept_associations", "concept_id"): ("identifier", "OMOP concept identifier"),
    ("concept_associations", "limit"): ("gap", "association result count"),
    ("concept_ancestors", "concept_id"): ("identifier", "OMOP concept identifier"),
    ("concept_candidate_bundle", "overall_limit"): ("gap", "total candidate count"),
    ("concept_candidate_bundle", "per_channel_limit"): (
        "gap",
        "per-source candidate count",
    ),
    ("concept_descendants", "concept_id"): ("identifier", "OMOP concept identifier"),
    ("concept_descendants", "offset"): ("gap", "number of descendants to skip"),
    ("concept_equivalency_path", "max_depth"): ("gap", "path search depth"),
    ("concept_equivalency_path", "source_id"): (
        "identifier",
        "source concept identifier",
    ),
    ("concept_equivalency_path", "target_id"): (
        "identifier",
        "target concept identifier",
    ),
    ("concept_extended_inheritance", "concept_id"): (
        "identifier",
        "OMOP concept identifier",
    ),
    ("concept_extended_inheritance", "limit"): ("gap", "inheritance result count"),
    ("concept_get", "concept_id"): ("identifier", "OMOP concept identifier"),
    ("concept_ground", "limit"): ("gap", "grounding candidate count"),
    ("concept_mapping_context", "concept_id"): (
        "identifier",
        "OMOP concept identifier",
    ),
    ("concept_mapping_context", "descendant_limit"): ("gap", "descendant count"),
    ("concept_mapping_context", "embedding_neighbor_limit"): (
        "gap",
        "embedding neighbor count",
    ),
    ("concept_mapping_context", "neighbor_limit"): ("gap", "graph neighbor count"),
    ("concept_nearest_standard_ancestor", "candidate_limit"): (
        "gap",
        "candidate count",
    ),
    ("concept_nearest_standard_ancestor", "concept_id"): (
        "identifier",
        "OMOP concept identifier",
    ),
    ("concept_neighbors", "concept_id"): ("identifier", "OMOP concept identifier"),
    ("concept_neighbors", "max_depth"): ("gap", "graph traversal depth"),
    ("concept_neighbors", "max_nodes"): ("gap", "graph node count"),
    ("concept_path", "max_depth"): ("gap", "path search depth"),
    ("concept_path", "source_id"): ("identifier", "source concept identifier"),
    ("concept_path", "target_id"): ("identifier", "target concept identifier"),
    ("concept_relationships", "concept_id"): ("identifier", "OMOP concept identifier"),
    ("concept_search_exact", "limit"): ("gap", "search result count"),
    ("concept_search_fulltext", "limit"): ("gap", "search result count"),
    ("concept_search_fulltext", "min_rank"): ("gap", "minimum relevance score"),
    ("concept_search_normalized", "limit"): ("gap", "search result count"),
    ("concept_shared_standard_ancestor", "offset"): (
        "gap",
        "number of candidates to skip",
    ),
    ("embedding_neighbours", "concept_id"): ("identifier", "OMOP concept identifier"),
    ("embedding_neighbours", "limit"): ("gap", "neighbor result count"),
    ("embedding_search", "limit"): ("gap", "search result count"),
    ("embedding_search_batch", "batch_size"): ("gap", "items per batch"),
    ("embedding_search_batch", "limit"): ("gap", "results per item"),
    ("mapping_evaluate_candidates", "top_k"): ("gap", "ranked candidate count"),
    ("text_decompose", "max_terms"): ("gap", "decomposed term count"),
    ("text_disambiguate", "max_interpretations"): ("gap", "interpretation count"),
}


@pytest.fixture(scope="module")
def surface():
    return reference_surface()


def test_agent_surface_matches_local_snapshot(surface):
    assert REFERENCE_PATH.is_file(), (
        f"Missing committed agent-surface snapshot. Regenerate it with `{REGENERATE_COMMAND}`."
    )
    differences = diff_surfaces(load_json(REFERENCE_PATH), surface)
    assert not differences, (
        "The agent-facing MCP surface changed:\n  "
        + "\n  ".join(differences)
        + f"\nIf intended, regenerate the local snapshot with `{REGENERATE_COMMAND}`."
    )


def test_agent_surface_stays_within_budget(surface):
    assert BUDGET_PATH.is_file(), (
        f"Missing committed agent-surface budget. Regenerate it with `{REGENERATE_COMMAND}`."
    )
    violation = budget_violation(surface, load_json(BUDGET_PATH))
    assert violation is None, violation


def test_unbounded_numeric_parameters_match_allowlist(surface):
    actual = {
        (tool_name, parameter)
        for tool_name, metrics in surface["tool_metrics"].items()
        for parameter in metrics["unbounded_numeric_params"]
    }
    expected = set(UNBOUNDED_NUMERIC_PARAM_ALLOWLIST)
    unexpected = sorted(actual - expected)
    newly_bounded = sorted(expected - actual)
    details = []
    if unexpected:
        details.append(
            "New unbounded parameters (bound these in the tool signature with "
            "`Field(ge=..., le=...)`): "
            + ", ".join(f"{tool}.{parameter}" for tool, parameter in unexpected)
        )
    if newly_bounded:
        details.append(
            "Allowlisted parameters that are now bounded (remove these entries): "
            + ", ".join(f"{tool}.{parameter}" for tool, parameter in newly_bounded)
        )
    assert actual == expected, "\n".join(details)


def test_unbounded_numeric_params_ignores_bounded_and_non_numeric():
    schema = {
        "properties": {
            "bounded": {"type": "integer", "minimum": 0, "maximum": 20},
            "lower_only": {"type": "integer", "minimum": 0},
            "upper_only": {"type": "integer", "maximum": 20},
            "open": {"type": "integer"},
            "optional_open": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "text": {"type": "string"},
        }
    }
    assert unbounded_numeric_params(schema) == [
        "lower_only",
        "open",
        "optional_open",
        "upper_only",
    ]


def test_diff_reports_added_removed_and_changed_tools():
    def tool(name, description):
        return {
            "name": name,
            "description": description,
            "inputSchema": {"properties": {}},
        }

    old = surface_document([tool("kept", "a"), tool("gone", "x")], [], [], [])
    new = surface_document([tool("kept", "changed"), tool("added", "y")], [], [], [])
    lines = diff_surfaces(old, new)
    assert "+ tool added" in lines
    assert "- tool gone" in lines
    assert any(line.startswith("~ tool kept: description") for line in lines)


def test_surface_diff_detects_changed_instructions_even_at_the_same_length():
    old = surface_document([], [], [], [], instructions="before")
    new = surface_document([], [], [], [], instructions="after!")

    assert diff_surfaces(old, new) == ["~ server instructions"]


@pytest.mark.parametrize(
    ("tool_chars", "instructions_chars", "expected"),
    [
        (100, 1050, None),
        (101, 952, "Model-visible tool surface"),
        (100, 1051, "Server instructions"),
    ],
)
def test_instruction_budget_is_separate_from_the_tool_budget(
    tool_chars, instructions_chars, expected
):
    document = {
        "summary": {
            "model_visible_tool_chars": tool_chars,
            "instructions_chars": instructions_chars,
        }
    }
    budget = {"max_model_visible_tool_chars": 100, "max_instructions_chars": 1050}
    violation = budget_violation(document, budget)
    if expected is None:
        assert violation is None
    else:
        assert violation is not None and violation.startswith(expected)


@pytest.mark.parametrize(
    "tool_name",
    (
        "concept_get",
        "concept_by_code",
        "concept_candidate_bundle",
        "concept_ground",
        "concept_search_exact",
        "concept_search_fulltext",
        "concept_search_normalized",
        "embedding_search",
        "concept_descendants",
        "concept_ancestors",
        "concept_mapping_context",
        "concept_nearest_standard_ancestor",
        "concept_navigate_to_standard",
        "concept_map_to_standard",
        "knowledge_catalogue",
        "knowledge_pack",
        "concept_extended_inheritance",
        "concept_neighbors",
        "concept_associations",
        "concept_equivalency_path",
        "concept_path",
        "system_status",
        "domain_classify",
        "embedding_encode",
        "mapping_evaluate_candidates",
        "source_plan_assisted",
        "text_decompose",
        "text_disambiguate",
        "text_mapping_cleanup",
        "text_normalize",
    ),
)
def test_mapping_workflow_tools_have_descriptions(surface, tool_name):
    tool = next(tool for tool in surface["tools"] if tool["name"] == tool_name)
    assert tool.get("description", "").strip()


@pytest.mark.parametrize(
    ("tool_name", "parameter_names"),
    [
        ("concept_get", None),
        ("concept_by_code", None),
        ("concept_candidate_bundle", None),
        ("concept_ground", None),
        ("concept_search_exact", None),
        ("concept_search_fulltext", None),
        ("concept_search_normalized", None),
        ("embedding_search", None),
        ("concept_descendants", None),
        ("concept_ancestors", None),
        ("concept_mapping_context", None),
        ("concept_nearest_standard_ancestor", None),
        ("concept_navigate_to_standard", None),
        ("concept_map_to_standard", None),
        ("knowledge_catalogue", None),
        ("knowledge_pack", None),
        ("domain_classify", ("label_values",)),
        ("embedding_encode", ("model_name",)),
        (
            "mapping_evaluate_candidates",
            ("predicted_mappings", "reference_mappings", "top_k"),
        ),
        ("source_plan_assisted", ("content", "caller_hint")),
        ("text_decompose", ("domain_hint", "max_terms")),
        ("text_disambiguate", ("domain_hint", "max_interpretations")),
        ("text_mapping_cleanup", ("context",)),
        ("text_normalize", ("domain_hint",)),
    ],
)
def test_mapping_workflow_tools_document_parameters(
    surface, tool_name, parameter_names
):
    tool = next(tool for tool in surface["tools"] if tool["name"] == tool_name)
    props = tool["inputSchema"]["properties"]
    for name in props if parameter_names is None else parameter_names:
        assert props[name].get("description", "").strip(), f"{tool_name}.{name}"
