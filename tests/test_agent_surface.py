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
    ),
)
def test_mapping_workflow_tools_have_descriptions(surface, tool_name):
    tool = next(tool for tool in surface["tools"] if tool["name"] == tool_name)
    assert tool.get("description", "").strip()


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
    ),
)
def test_mapping_workflow_tools_document_all_parameters(surface, tool_name):
    tool = next(tool for tool in surface["tools"] if tool["name"] == tool_name)
    for name, prop in tool["inputSchema"]["properties"].items():
        assert prop.get("description", "").strip(), f"{tool_name}.{name}"
