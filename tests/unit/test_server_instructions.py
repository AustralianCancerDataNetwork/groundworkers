"""Registration-aware instructions as received through MCP initialization."""

import ast
import json
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.server import create_server, main
from groundworkers.server_instructions import (
    INSTRUCTION_SECTIONS,
    TOOL_CATALOGUE,
    build_instructions,
    instruction_requirements,
)
from groundworkers.tools.concept_tools import register_concept_tools
from groundworkers.tools.system_tools import register_system_tools
from tests.support.agent_surface import collect_surface, reference_server
from tests.support.stack_config import build_cdm_stack

SECTIONS = dict(INSTRUCTION_SECTIONS)
REQUIREMENTS = {
    "intro": {"system_status"},
    "knowledge": {"knowledge_catalogue", "knowledge_pack"},
    "mapping": {
        "concept_candidate_bundle",
        "concept_mapping_context",
        "concept_descendants",
        "concept_nearest_standard_ancestor",
    },
}
EXPECTED_TEXT = "\n\n".join(SECTIONS.values()) + "\n"


def _stub_tool() -> dict:
    return {}


def test_catalogue_matches_every_builtin_tool_registration():
    registered = set()
    for path in files("groundworkers.tools").iterdir():
        if not path.name.endswith(".py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "tool"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                registered.add(node.args[0].value)
    assert TOOL_CATALOGUE == registered
    for name, text in INSTRUCTION_SECTIONS:
        assert instruction_requirements(text) == REQUIREMENTS[name]
        assert REQUIREMENTS[name] <= registered


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("concept_id domain_id", set()),
        ("embedding_search_batch", {"embedding_search_batch"}),
        (
            "(concept_mapping_context, concept_descendants):",
            {"concept_mapping_context", "concept_descendants"},
        ),
    ],
)
def test_requirements_match_catalogue_names_without_treating_fields_as_tools(
    text, expected
):
    assert instruction_requirements(text) == expected


@pytest.mark.parametrize("missing", sorted(set().union(*REQUIREMENTS.values())))
def test_every_named_tool_is_required_before_its_section_is_sent(missing):
    registered = TOOL_CATALOGUE - {missing}
    text, names = build_instructions(registered)
    for name in names:
        assert REQUIREMENTS[name] <= registered
    assert names == tuple(
        name for name in SECTIONS if missing not in REQUIREMENTS[name]
    )
    assert text == "\n\n".join(SECTIONS[name] for name in names) + "\n"


@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable-http"])
def test_full_reference_initialize_receives_exact_sections_for_every_transport(
    transport,
):
    with reference_server() as server:
        surface = collect_surface(server, transport=transport)
        assert server.describe_instructions() == {
            "present": True,
            "chars": 952,
            "sections": ["intro", "knowledge", "mapping"],
        }
    assert surface["instructions"] == EXPECTED_TEXT
    assert surface["summary"]["instructions_chars"] == len(EXPECTED_TEXT) == 952
    assert surface["summary"]["instructions_chars"] <= 1050
    registered = {tool["name"] for tool in surface["tools"]}
    for text in SECTIONS.values():
        assert instruction_requirements(text) <= registered


def test_initialize_without_knowledge_receives_intro_and_mapping():
    with (
        patch("groundworkers.server.register_knowledge_tools", return_value=False),
        reference_server() as server,
    ):
        surface = collect_surface(server)
    assert (
        surface["instructions"]
        == SECTIONS["intro"] + "\n\n" + SECTIONS["mapping"] + "\n"
    )
    assert "knowledge_catalogue" not in {tool["name"] for tool in surface["tools"]}


def test_graph_without_mapping_or_knowledge_receives_intro_only():
    server = GroundworkersMCPServer("graph-only")
    register_concept_tools(server, Mock())
    register_system_tools(server)
    assert "concept_descendants" in server.list_tools()
    assert "concept_candidate_bundle" not in server.list_tools()
    surface = collect_surface(server)
    assert surface["instructions"] == SECTIONS["intro"] + "\n"


@pytest.mark.parametrize("tools", [(), ("custom_plugin_tool",)])
def test_initialize_without_an_applicable_section_receives_none(tools):
    server = GroundworkersMCPServer("no-instructions")
    for name in tools:
        server.tool(name)(_stub_tool)
    surface = collect_surface(server)
    assert surface["instructions"] is None
    assert surface["summary"]["instructions_chars"] == 0
    assert server.describe_instructions() == {
        "present": False,
        "chars": 0,
        "sections": [],
    }


def test_instructions_are_selected_after_plugin_tool_registration():
    config = build_app_config_from_stack(build_cdm_stack())

    def register(server, state):
        for name in REQUIREMENTS["mapping"] - {"concept_descendants"}:
            server.tool(name)(_stub_tool)

    plugin = SimpleNamespace(name="late-tools", register=register)
    app = SimpleNamespace(
        services=SimpleNamespace(
            graph=Mock(),
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
        plugin_definitions=(plugin,),
        plugins={plugin.name: object()},
        plugin_issues={},
    )
    server = create_server(config, app)
    assert server.describe_instructions()["sections"] == [
        "intro",
        "knowledge",
        "mapping",
    ]
    assert collect_surface(server)["instructions"] == EXPECTED_TEXT


def test_describe_reports_instruction_presence_length_and_sections(monkeypatch, capsys):
    config = build_app_config_from_stack(build_cdm_stack())
    app = SimpleNamespace(plugins={}, plugin_issues={})
    with reference_server() as server:
        monkeypatch.setattr(
            "groundworkers.server.build_app_config", lambda **kwargs: config
        )
        monkeypatch.setattr("groundworkers.server.build_application", lambda cfg: app)
        monkeypatch.setattr(
            "groundworkers.server.create_server", lambda cfg, application: server
        )
        main(["--describe"])

    description = json.loads(capsys.readouterr().out)
    assert description["instructions"] == {
        "present": True,
        "chars": 952,
        "sections": ["intro", "knowledge", "mapping"],
    }
