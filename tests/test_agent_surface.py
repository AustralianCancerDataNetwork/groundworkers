"""T0 agent-surface gate: what MCP clients are shown may only change on purpose.

See ``tests/support/agent_surface.py``. When a change to the surface is
intended, regenerate the snapshot and commit it with the change, so the
diff is reviewed alongside the code.
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


def test_agent_surface_matches_committed_snapshot(surface):
    differences = diff_surfaces(load_json(REFERENCE_PATH), surface)
    assert not differences, (
        "The agent-facing MCP surface changed:\n  "
        + "\n  ".join(differences)
        + f"\nIf intended, run `{REGENERATE_COMMAND}` and commit the snapshot."
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
        return {"name": name, "description": description, "inputSchema": {"properties": {}}}

    old = surface_document([tool("kept", "a"), tool("gone", "x")], [], [], [])
    new = surface_document([tool("kept", "changed"), tool("added", "y")], [], [], [])
    lines = diff_surfaces(old, new)
    assert "+ tool added" in lines
    assert "- tool gone" in lines
    assert any(line.startswith("~ tool kept: description") for line in lines)
