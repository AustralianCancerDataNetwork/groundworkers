"""Toolset declarations, selection and plugin registration defaults."""

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.server import create_server
from groundworkers.server_instructions import build_instructions
from groundworkers.services.semantic_projection.service import SemanticProjectionService
from groundworkers.tools.semantic_projection_tools import (
    register_semantic_projection_tools,
)
from groundworkers.toolsets import TOOLSET_DESCRIPTIONS, ToolMeta, Toolset, select_tools
from tests.support.agent_surface import collect_surface, diff_surfaces, reference_server
from tests.support.stack_config import build_cdm_stack

TAXONOMY = json.loads(
    (Path(__file__).parents[1] / "support" / "toolsets.json").read_text(
        encoding="utf-8"
    )
)
TOOL_TOOLSETS = TAXONOMY["tools"]
PROFILES = json.loads(
    (Path(__file__).parents[1] / "support" / "toolset_profiles.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture(scope="module")
def reference_toolsets():
    with reference_server() as server:
        toolsets = {
            name: item["toolsets"] for name, item in server.describe_tools().items()
        }
    assert len(toolsets) == 39
    return toolsets


def _stub_tool() -> dict:
    return {}


def test_every_builtin_tool_declares_exactly_its_known_toolsets():
    assert dict(TOOLSET_DESCRIPTIONS) == TAXONOMY["toolsets"]
    assert set(Toolset) == set(TAXONOMY["toolsets"])
    with reference_server() as server:
        register_semantic_projection_tools(server, SemanticProjectionService())
        described = server.describe_tools()
    assert len(described) == 40
    assert {name: item["toolsets"] for name, item in described.items()} == {
        name: sorted(tags) for name, tags in TOOL_TOOLSETS.items()
    }
    for item in described.values():
        assert item["toolsets"]
        assert set(item["toolsets"]) <= set(TOOLSET_DESCRIPTIONS)


@pytest.mark.parametrize(
    ("include", "exclude", "expected"),
    [
        (None, (), {"shared", "search", "untagged"}),
        ((), (), set()),
        ({"unknown"}, (), set()),
        (["mapping"], (), {"shared"}),
        ({"search", "graph"}, (), {"shared", "search"}),
        (None, ["graph"], {"search", "untagged"}),
        ({"graph"}, {"mapping"}, set()),
        ({"search", "graph"}, {"mapping"}, {"search"}),
        (None, {"unknown"}, {"shared", "search", "untagged"}),
        (None, {"search", "graph"}, {"untagged"}),
    ],
)
def test_selection_uses_any_membership_and_none_includes_untagged(
    include, exclude, expected
):
    mapping = {"shared": {"mapping", "graph"}, "search": ["search"], "untagged": ()}
    result = select_tools(mapping, include=include, exclude=exclude)
    assert isinstance(result, frozenset)
    assert result == expected


def test_builtin_multi_membership_selects_mapping_and_graph_independently():
    mapping = select_tools(TOOL_TOOLSETS, include={"mapping"})
    graph = select_tools(TOOL_TOOLSETS, include={"graph"})
    assert {"concept_descendants", "concept_candidate_bundle"} <= mapping
    assert "concept_descendants" in graph
    assert "concept_candidate_bundle" not in graph


@pytest.mark.parametrize(
    ("requested", "sections"),
    [
        ({"system", "knowledge", "mapping"}, ("intro", "knowledge", "mapping")),
        ({"system", "graph"}, ("intro",)),
        ({"mapping"}, ("mapping",)),
    ],
)
def test_selected_tools_drive_instruction_composition(requested, sections):
    _, included = build_instructions(select_tools(TOOL_TOOLSETS, include=requested))
    assert included == sections


def test_selection_defaults_to_all_reference_tools(reference_toolsets):
    assert select_tools(reference_toolsets) == frozenset(reference_toolsets)


def test_deterministic_only_selection(reference_toolsets):
    profile = PROFILES["deterministic"]
    selected = select_tools(
        reference_toolsets, include=profile["include"], exclude=profile["exclude"]
    )
    assert selected == frozenset(profile["tools"])
    assert len(selected) == 31


def test_mapping_workflow_selection_includes_all_instruction_sections(
    reference_toolsets,
):
    profile = PROFILES["mapping_workflow"]
    selected = select_tools(
        reference_toolsets, include=profile["include"], exclude=profile["exclude"]
    )
    assert selected == frozenset(profile["tools"])
    assert len(selected) == 12
    assert build_instructions(selected)[1] == tuple(profile["sections"])


def test_excluding_graph_overrides_mapping_and_drops_mapping_instructions(
    reference_toolsets,
):
    selected = select_tools(reference_toolsets, include={"mapping"}, exclude={"graph"})
    assert not {"concept_ancestors", "concept_descendants"} & selected
    assert "mapping" not in build_instructions(selected)[1]


def test_registration_freezes_memberships_and_metadata():
    server = GroundworkersMCPServer("metadata")
    memberships = {"custom", "mapping"}
    server.tool("tagged", toolsets=memberships)(_stub_tool)
    memberships.add("later")
    metadata = server._tool_metadata["tagged"]
    assert metadata == ToolMeta(frozenset({"custom", "mapping"}))
    with pytest.raises(FrozenInstanceError):
        metadata.toolsets = frozenset()
    assert server.describe_tools()["tagged"]["toolsets"] == ["custom", "mapping"]


def test_available_tools_calls_selection_with_none():
    server = GroundworkersMCPServer("available")
    server.tool("tagged", toolsets=["custom"])(_stub_tool)
    server.tool("untagged")(_stub_tool)
    with patch("groundworkers.base.server.select_tools", wraps=select_tools) as select:
        assert server.available_tools() == {"tagged", "untagged"}
    select.assert_called_once_with(
        {"tagged": frozenset({"custom"}), "untagged": frozenset()},
        include=None,
        exclude=(),
    )


def test_available_tools_drives_discovery_instructions_and_description(monkeypatch):
    server = GroundworkersMCPServer("filtered")
    server.tool("knowledge_pack", toolsets=["knowledge"])(_stub_tool)
    server.tool("system_status", toolsets=["system"])(_stub_tool)
    server.tool("knowledge_catalogue", toolsets=["knowledge"])(_stub_tool)
    monkeypatch.setattr(server, "available_tools", lambda: frozenset({"system_status"}))
    surface = collect_surface(server)
    assert server.list_tools() == ["system_status"]
    assert list(server.describe_tools()) == ["system_status"]
    assert [tool["name"] for tool in surface["tools"]] == ["system_status"]
    assert server.describe_instructions()["sections"] == ["intro"]
    assert surface["instructions"] == server.instructions
    assert surface["toolsets"] == {"system_status": ["system"]}
    assert "toolsets" not in surface["tools"][0]
    assert "_meta" not in surface["tools"][0]


def test_fastmcp_keeps_registration_order():
    server = GroundworkersMCPServer("ordered")
    for name in ("z_last_alphabetically", "a_first_alphabetically", "middle"):
        server.tool(name)(_stub_tool)
    with patch.object(FastMCP, "run", autospec=True) as run:
        server.run()
    app = run.call_args.args[0]
    assert [tool.name for tool in asyncio.run(app.list_tools())] == [
        "z_last_alphabetically",
        "a_first_alphabetically",
        "middle",
    ]


def test_plugin_context_restores_nested_and_failed_registrations():
    server = GroundworkersMCPServer("nested")
    with server.plugin_registration("outer"):
        with pytest.raises(RuntimeError), server.plugin_registration("inner"):
            server.tool("inner")(_stub_tool)
            raise RuntimeError("registration failed")
        server.tool("outer")(_stub_tool)
    server.tool("outside")(_stub_tool)
    assert server.describe_tools()["inner"]["toolsets"] == ["plugin:inner"]
    assert server.describe_tools()["outer"]["toolsets"] == ["plugin:outer"]
    assert server.describe_tools()["outside"]["toolsets"] == []


def test_create_server_applies_plugin_defaults_and_explicit_tags_after_failure():
    def register_failing(server, state):
        server.tool("defaulted")(_stub_tool)
        raise RuntimeError("registration failed")

    def register_custom(server, state):
        server.tool("custom", toolsets={"custom:search", "search"})(_stub_tool)
        server.tool("next_default", toolsets=[])(_stub_tool)

    plugins = (
        SimpleNamespace(name="failing", register=register_failing),
        SimpleNamespace(name="custom", register=register_custom),
    )
    app = SimpleNamespace(
        services=SimpleNamespace(
            graph=None,
            vocab=None,
            mapping=None,
            grounding=None,
            text=None,
            domain=None,
            source_planning=None,
        ),
        adapters=SimpleNamespace(
            omop_graph=None,
            omop_emb=None,
            llm=None,
            embedding_configuration_detail=None,
        ),
        plugin_definitions=plugins,
        plugins={plugin.name: object() for plugin in plugins},
        plugin_issues={},
    )
    server = create_server(build_app_config_from_stack(build_cdm_stack()), app)
    server.tool("outside")(_stub_tool)
    described = server.describe_tools()
    assert described["defaulted"]["toolsets"] == ["plugin:failing"]
    assert described["custom"]["toolsets"] == ["custom:search", "search"]
    assert described["next_default"]["toolsets"] == ["plugin:custom"]
    assert described["outside"]["toolsets"] == []
    assert app.plugin_issues == {"failing": "plugin registration failed"}
    assert set(app.plugins) == {"custom"}


def test_surface_diff_reports_metadata_without_changing_character_metrics():
    server = GroundworkersMCPServer("snapshot")
    server.tool("example", toolsets=["before"])(_stub_tool)
    before = collect_surface(server)
    server.tool("example", toolsets=["after"])(_stub_tool)
    after = collect_surface(server)
    assert before["summary"] == after["summary"]
    assert before["tool_metrics"] == after["tool_metrics"]
    assert before["tools"] == after["tools"]
    assert diff_surfaces(before, after) == ["~ tool example: toolsets"]
