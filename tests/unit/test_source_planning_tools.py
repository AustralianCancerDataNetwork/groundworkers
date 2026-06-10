from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.server import GroundcrewServer
from groundworkers.services.source_planning import SourcePlanningService
from groundworkers.tools.source_planning_tools import (
    register_source_planning_resources,
    register_source_planning_tools,
)


def _server() -> GroundcrewServer:
    server = GroundcrewServer("test-server")
    register_source_planning_tools(server, SourcePlanningService())
    register_source_planning_resources(server)
    return server


def test_source_plan_returns_serialized_plan_for_utf8_csv() -> None:
    result = _server().call(
        "source_plan",
        content="code,label\nE11.9,Type 2 diabetes mellitus\n",
        filename="diagnoses.csv",
        caller_hint="data_dict",
    )

    assert result["plan"]["format_detected"] == "CSV"
    assert result["plan"]["strategies"] == ["DATA_DICT_IDEAL"]
    assert result["plan"]["tables"][0]["column_annotations"]["code"]["role"] == "codes"
    assert result["raw_tables"] is None
    assert result["normalised_tables"] is None
    assert result["annotated_tables"] is None


def test_source_plan_decodes_base64_when_requested() -> None:
    result = _server().call(
        "source_plan",
        content="Y29kZSxsYWJlbApFMTEuOSxUeXBlIDIgZGlhYmV0ZXMgbWVsbGl0dXMK",
        filename="diagnoses.csv",
        caller_hint="data_dict",
        content_encoding="base64",
        include_intermediate=True,
    )

    assert result["plan"]["strategies"] == ["DATA_DICT_IDEAL"]
    assert result["raw_tables"][0]["name"] == "diagnoses"
    assert result["annotated_tables"][0]["column_annotations"]["label"]["role"] == "label"


def test_source_plan_rejects_invalid_base64() -> None:
    result = _server().call(
        "source_plan",
        content="not-base64!!!",
        filename="bad.csv",
        content_encoding="base64",
    )

    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"


def test_source_planning_resources_are_registered_and_json_readable() -> None:
    server = _server()

    assert set(server.list_resources()) == {
        "source-planning://canonical-headers",
        "source-planning://column-roles",
        "source-planning://ingestion-strategies",
    }

    headers = json.loads(server._resources["source-planning://canonical-headers"][0]())
    roles = json.loads(server._resources["source-planning://column-roles"][0]())
    strategies = json.loads(server._resources["source-planning://ingestion-strategies"][0]())

    assert headers["field_label"]["role"] == "label"
    assert roles["codes"]["description"]
    assert strategies["DATA_DICT_PACKED_VALUES"]["description"]
