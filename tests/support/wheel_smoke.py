"""Smoke-test an installed wheel while running outside the source checkout."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path


def main() -> None:
    mode = sys.argv[1]
    import groundworkers

    checkout = Path(__file__).resolve().parents[2]
    installed = Path(groundworkers.__file__).resolve()
    assert not installed.is_relative_to(checkout), (
        f"wheel smoke imported checkout source: {installed}"
    )
    entries = importlib.metadata.entry_points(group="omop.config")
    assert any(
        entry.name == "groundworkers"
        and entry.value == "groundworkers.config:GroundworkersConfig"
        for entry in entries
    )
    subprocess.run(
        [sys.executable, "-m", "groundworkers.server", "--help"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if mode == "extras":
        _exercise_transports()


def _exercise_transports() -> None:
    from fastapi.testclient import TestClient
    from oa_configurator import CDMDatabaseConfig, ConnectionConfig, StackConfig

    from groundworkers.app import Adapters, GroundworkersApp, Services
    from groundworkers.base.server import GroundworkersMCPServer
    from groundworkers.bootstrap import build_app_config_from_stack
    from groundworkers.services.source_planning.assisted import (
        AssistedColumnRoleClassifier,
    )
    from groundworkers.services.source_planning.service import SourcePlanningService
    from groundworkers.tools.source_planning_tools import register_source_planning_tools
    from groundworkers.transports.rest import create_rest_app

    class FixedModel:
        def complete_structured(self, _prompt, _schema, **_kwargs):
            return {"decisions": [{"header": "unknown_a", "role": "values", "confidence": 0.9}]}

        async def async_complete_structured(self, _prompt, _schema, **_kwargs):
            return self.complete_structured(_prompt, _schema, **_kwargs)

    service = SourcePlanningService(
        assisted_classifier=AssistedColumnRoleClassifier(FixedModel())
    )
    server = GroundworkersMCPServer("wheel-smoke")
    register_source_planning_tools(server, service)
    mcp_result = server.call(
        "source_plan_assisted",
        content="unknown_a,unknown_b\nA,B\n",
        filename="fixture.csv",
        include_intermediate=True,
    )
    assert mcp_result["llm_tier_used"] is True

    stack = StackConfig(
        connections={"test": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm": CDMDatabaseConfig(connection="test")},
        tools={"groundworkers": {"cdm_db": "cdm"}},
    )
    app = GroundworkersApp(
        config=build_app_config_from_stack(stack),
        adapters=Adapters(),
        services=Services(source_planning=service),
    )
    client = TestClient(create_rest_app(app))
    response = client.post(
        "/v1/source-planning/assisted-plan",
        json={
            "content": "unknown_a,unknown_b\nA,B\n",
            "filename": "fixture.csv",
            "include_intermediate": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["llm_tier_used"] is True


if __name__ == "__main__":
    main()
