"""MCP schema guards and optional local surface comparisons.

Schema guards always run. Snapshot and budget checks use optional local
artifacts generated with ``tests/support/agent_surface.py``.
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


@pytest.fixture(scope="module")
def surface():
    return reference_surface()


@pytest.mark.skipif(
    not REFERENCE_PATH.is_file(), reason="Local surface snapshot is unavailable"
)
def test_agent_surface_matches_local_snapshot(surface):
    differences = diff_surfaces(load_json(REFERENCE_PATH), surface)
    assert not differences, (
        "The agent-facing MCP surface changed:\n  "
        + "\n  ".join(differences)
        + f"\nIf intended, regenerate the local snapshot with `{REGENERATE_COMMAND}`."
    )


@pytest.mark.skipif(
    not BUDGET_PATH.is_file(), reason="Local surface budget is unavailable"
)
def test_agent_surface_stays_within_budget(surface):
    violation = budget_violation(surface, load_json(BUDGET_PATH))
    assert violation is None, violation


def test_unbounded_numeric_params_ignores_bounded_and_non_numeric():
    schema = {
        "properties": {
            "bounded": {"type": "integer", "minimum": 0, "maximum": 20},
            "open": {"type": "integer"},
            "optional_open": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "text": {"type": "string"},
        }
    }
    assert unbounded_numeric_params(schema) == ["open", "optional_open"]


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
