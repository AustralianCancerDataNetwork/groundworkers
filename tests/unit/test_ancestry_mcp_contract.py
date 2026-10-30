"""MCP discovery and actual in-memory client/server wire contracts."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy.exc import OperationalError

from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.graph import GraphService
from groundworkers.services.mapping import MappingService
from groundworkers.tools.concept_tools import register_concept_tools
from groundworkers.tools.mapping_tools import register_mapping_tools


class StoredAdapter:
    def __init__(self):
        self.views = {
            cid: {
                "concept_id": cid,
                "concept_name": str(cid),
                "concept_code": str(cid),
                "domain_id": "Drug",
                "vocabulary_id": "RxNorm",
                "is_active": True,
                "standard_concept": cid >= 10,
                "concept_class_id": "Branded Drug" if cid == 20 else "Clinical Drug",
            }
            for cid in [1, 2, 10, 20, 30]
        }
        self.rows = [
            (1, 10, 0),
            (2, 10, 1),
            (1, 20, 1),
            (2, 20, 1),
            (1, 30, 2),
            (2, 30, 2),
        ]

    def ancestor_rows(self, ids, *, max_separation):
        return [
            {
                "descendant_concept_id": cid,
                "ancestor_concept_id": aid,
                "min_levels_of_separation": depth,
                "max_levels_of_separation": depth,
            }
            for cid, aid, depth in self.rows
            if cid in ids and depth <= max_separation
        ]

    def concept_views(self, ids, *, strict=False):
        return {cid: self.views[cid] for cid in ids if cid in self.views}

    def get_concept(self, cid):
        return self.views.get(cid)

    def validate_ancestor_constraints(self, classes, vocabs):
        if classes and set(classes) - {"Clinical Drug", "Branded Drug"}:
            raise ValueError("Unknown concept classes")
        if vocabs and set(vocabs) - {"RxNorm", "SNOMED"}:
            raise ValueError("Unknown vocabularies")


@pytest.fixture
def stack(monkeypatch):
    graph = GraphService(StoredAdapter())
    mapping = MappingService(Mock(), graph_service=graph)
    server = GroundworkersMCPServer("ancestry-wire")
    register_concept_tools(server, graph)
    register_mapping_tools(server, mapping)
    captured = {}
    monkeypatch.setattr(FastMCP, "run", lambda app, **kwargs: captured.update(app=app))
    server.run()
    return server, captured["app"], mapping, graph


def _payload(result):
    assert not result.isError
    structured = result.structuredContent
    assert json.loads(result.content[0].text) == structured
    return structured


def test_native_discovery_advertises_bounds_typed_envelope_and_annotations(stack):
    server, app, _, _ = stack

    async def run():
        async with create_connected_server_and_client_session(app) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            tool = tools["concept_shared_standard_ancestor"]
            props = tool.inputSchema["properties"]
            assert props["concept_ids"]["minItems"] == 2
            assert props["concept_ids"]["maxItems"] == 200
            assert props["concept_ids"]["items"]["exclusiveMinimum"] == 0
            assert props["max_depth"]["minimum"] == 0
            assert props["max_depth"]["maximum"] == 20
            assert props["limit"]["maximum"] == 50
            assert "data" in tool.outputSchema["properties"]
            assert "result" not in tool.outputSchema["properties"]
            assert tool.annotations.readOnlyHint
            assert tool.annotations.idempotentHint
            assert not tool.annotations.destructiveHint
            assert not tool.annotations.openWorldHint

    asyncio.run(run())
    assert server.describe_tools()["concept_shared_standard_ancestor"]["annotations"][
        "readOnlyHint"
    ]


def test_native_round_trip_preserves_tie_when_page_hides_alternatives(stack):
    _, app, _, _ = stack

    async def run():
        async with create_connected_server_and_client_session(app) as client:

            def call(args):
                return client.call_tool("concept_shared_standard_ancestor", args)

            data = _payload(await call({"concept_ids": [1, 2], "limit": 1}))["data"]
            assert data["status"] == "ambiguous"
            assert data["selected_ancestor"] is None
            assert data["nearest_candidate_count"] == 2
            assert data["common_candidate_count"] == 3
            assert data["page"]["has_more"]
            assert len(data["common_ancestors"]) == 1
            data = _payload(
                await call(
                    {
                        "concept_ids": [2, 1, 2],
                        "concept_class_ids": [" Clinical Drug "],
                        "limit": 1,
                        "offset": 50,
                    }
                )
            )["data"]
            assert data["status"] == "selected"
            assert data["selected_ancestor"]["concept_id"] == 10
            assert data["selected_distances_by_input"] == {"1": 0, "2": 1}
            assert data["common_ancestors"] == []
            assert data["selection_complete"]

    asyncio.run(run())


def test_native_schema_errors_are_distinct_from_service_validation_errors(stack):
    _, app, _, _ = stack

    async def run():
        async with create_connected_server_and_client_session(app) as client:
            for args in [
                {"concept_ids": [True, 2]},
                {"concept_ids": [1.0, 2]},
                {"concept_ids": ["1", 2]},
                {"concept_ids": [1]},
                {"concept_ids": [1, 2], "max_depth": -1},
                {"concept_ids": [1, 2], "max_depth": 21},
                {"concept_ids": [1, 2], "limit": 0},
                {"concept_ids": [1, 2], "concept_class_ids": []},
                {"concept_ids": [1] * 201},
            ]:
                assert (
                    await client.call_tool("concept_shared_standard_ancestor", args)
                ).isError
            for args in [
                {"concept_ids": [1, 1]},
                {"concept_ids": [1, 2], "concept_class_ids": ["no-such-class"]},
                {"concept_ids": [1, 2], "concept_class_ids": [" "]},
            ]:
                data = _payload(
                    await client.call_tool("concept_shared_standard_ancestor", args)
                )
                assert data["error"] and data["code"] == "INVALID_INPUT"
                assert data["data"] is None

    asyncio.run(run())


def test_no_match_mixed_domains_unknown_seed_and_backend_failure(stack, monkeypatch):
    server, _, mapping, graph = stack
    tool = "concept_shared_standard_ancestor"
    assert (
        server.call(tool, concept_ids=[1, 2], max_depth=0)["data"]["status"]
        == "no_match"
    )
    graph._adapter.views[2]["domain_id"] = "Condition"
    assert (
        server.call(tool, concept_ids=[1, 2])["data"]["status"] == "incompatible_inputs"
    )
    assert server.call(tool, concept_ids=[1, 99])["code"] == "NOT_FOUND"

    def timeout(*args, **kwargs):
        raise GroundworkersError("QUERY_TIMEOUT", "query cancelled")

    monkeypatch.setattr(mapping, "concept_shared_standard_ancestor", timeout)
    result = server.call(tool, concept_ids=[1, 2])
    assert result["code"] == "QUERY_TIMEOUT" and result["data"] is None


def test_actual_wire_error_and_malformed_output_have_no_selection(stack, monkeypatch):
    _, app, mapping, _ = stack

    def failure(*args, **kwargs):
        raise GroundworkersError("BACKEND_UNAVAIL", "password=secret")

    async def call():
        async with create_connected_server_and_client_session(app) as client:
            return _payload(
                await client.call_tool(
                    "concept_shared_standard_ancestor", {"concept_ids": [1, 2]}
                )
            )

    monkeypatch.setattr(mapping, "concept_shared_standard_ancestor", failure)
    result = asyncio.run(call())
    assert result["data"] is None and result["code"] == "BACKEND_UNAVAIL"
    assert "secret" not in result["message"]
    monkeypatch.setattr(
        mapping,
        "concept_shared_standard_ancestor",
        lambda *a, **kw: {"selected_ancestor": {"concept_id": 10}},
    )
    result = asyncio.run(call())
    assert result["data"] is None and result["code"] == "INTERNAL_ERROR"


def test_zero_distance_is_visible_consistently_and_context_depth_is_independent(stack):
    server, _, mapping, _ = stack
    ancestors = server.call("concept_ancestors", concept_id=1, max_depth=0)
    assert [row["concept_id"] for row in ancestors["ancestors"]] == [10]
    assert ancestors["distance_metric"] == "min_levels_of_separation"
    result = mapping.concept_mapping_context(
        1,
        include_standard_mapping=False,
        include_relationship_summary=False,
        include_neighbors=False,
        ancestor_limit=1,
        ancestor_max_depth=2,
    )
    assert result["ancestors"][0]["concept_id"] == 10
    assert result["ancestor_scope"]["total"] == 3
    assert result["ancestor_scope"]["truncated"]


def test_single_nearest_tool_preserves_tie_and_path_failure_is_only_diagnostic(
    stack, monkeypatch
):
    _, _, mapping, graph = stack
    generic = mapping.concept_nearest_standard_ancestor(concept_id=2)
    assert generic["status"] == "ambiguous" and generic["selected_parent"] is None

    def fail_path(*args, **kwargs):
        raise GroundworkersError("QUERY_TIMEOUT", "path timeout")

    monkeypatch.setattr(graph, "find_path", fail_path)
    result = mapping.concept_nearest_standard_ancestor(concept_id=1, max_depth=0)
    assert result["selected_parent"]["concept_id"] == 10
    assert result["path_status"] == "unavailable"
    assert result["path_error"]["code"] == "QUERY_TIMEOUT"


def test_database_cancel_is_classified_as_timeout():
    class Cancelled(Exception):
        sqlstate = "57014"

    error = OperationalError("query", {}, Cancelled())
    result = OmopGraphAdapter._wrap_graph_error(error, default_code="QUERY_ERROR")
    assert result.code == "QUERY_TIMEOUT"



def test_async_guard_normalizes_typed_service_errors_and_success(stack):
    from groundworkers.base.ancestry import SharedAncestorResponse
    server, _, mapping, _ = stack

    @server.tool("async_ancestry_contract", result_model=SharedAncestorResponse)
    async def async_ancestry_contract(fail: bool = False):
        if fail:
            raise GroundworkersError("QUERY_TIMEOUT", "query cancelled")
        return {"data": mapping.concept_shared_standard_ancestor([1, 2])}

    async def run():
        success = await server.call_async("async_ancestry_contract")
        assert success["data"]["status"] == "ambiguous"
        failure = await server.call_async("async_ancestry_contract", fail=True)
        assert failure["code"] == "QUERY_TIMEOUT"
        assert failure["data"] is None
    asyncio.run(run())


def test_incomplete_selected_distance_evidence_becomes_internal_error(stack, monkeypatch):
    server, _, mapping, _ = stack
    result = mapping.concept_shared_standard_ancestor([1, 2], concept_class_ids=["Clinical Drug"])
    result["selected_distances_by_input"].pop("2")
    monkeypatch.setattr(mapping, "concept_shared_standard_ancestor", lambda *a, **kw: result)
    response = server.call("concept_shared_standard_ancestor", concept_ids=[1, 2])
    assert response["code"] == "INTERNAL_ERROR"
    assert response["data"] is None
