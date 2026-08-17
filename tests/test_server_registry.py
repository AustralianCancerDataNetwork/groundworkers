import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from groundworkers.app import build_adapters, build_application
from groundworkers.base.server import GroundcrewServer
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.server import create_server, main, parse_args, run_rest_api
from tests.support.stack_config import build_cdm_stack, build_embedding_stack


def _stack_without_backends():
    return build_cdm_stack()


def _stack_with_cdm():
    stack = build_cdm_stack(schema_name="omop", vocab_schema="omop_vocab")
    stack.tools["omop_graph"] = {}
    return stack


def test_server_starts_with_cdm_only_without_graph_or_embedding_tools():
    config = build_app_config_from_stack(_stack_without_backends())
    server = create_server(config)
    assert "concept_search_exact" in server.list_tools()
    assert "concept_get" not in server.list_tools()
    assert "embedding_index_status" not in server.list_tools()
    assert "config://active" in server.list_resources()


def test_server_registers_concept_tools_when_cdm_resource_is_configured():
    config = build_app_config_from_stack(_stack_with_cdm())
    server = create_server(config)
    names = server.list_tools()
    assert "concept_get" in names
    assert "concept_by_code" in names
    assert "concept_ancestors" in names
    assert "concept_descendants" in names


def test_server_registers_embedding_tools_when_enabled(tmp_path: Path):
    stack = build_embedding_stack()
    stack.connections["embedding_main"].database_name = str(tmp_path / "omop_emb.db")
    config = build_app_config_from_stack(stack)
    server = create_server(config)
    names = server.list_tools()
    assert "embedding_index_status" in names
    assert "embedding_neighbours" in names


def test_build_adapters_leaves_disabled_components_unset():
    config = build_app_config_from_stack(_stack_without_backends())
    adapters = build_adapters(config)
    assert adapters.cdm is not None
    assert adapters.omop_graph is None
    assert adapters.omop_emb is None


def test_build_application_exposes_services_container():
    config = build_app_config_from_stack(_stack_without_backends())
    app = build_application(config)
    assert app.adapters.omop_graph is None
    assert app.services.mapping is not None
    assert app.services.source_planning is not None


def test_runtime_config_masks_api_keys(tmp_path: Path):
    stack = build_embedding_stack()
    stack.tools["groundworkers"]["llm"] = {
        "enabled": True,
        "api_base": "http://example.test/v1",
        "api_key": "secret",
    }
    stack.providers["embedding_provider"].api_key = "emb-secret"
    stack.connections["embedding_main"].database_name = str(tmp_path / "omop_emb.db")
    config = build_app_config_from_stack(stack)

    described = config.describe()

    assert described["groundworkers"]["llm"]["api_key"] == "***"
    assert described["model"]["provider"]["api_key"] == "***"


def test_embedding_store_is_resolved_lazily_and_shared(monkeypatch, tmp_path: Path):
    stack = build_embedding_stack()
    stack.connections["embedding_main"].database_name = str(tmp_path / "emb.db")
    config = build_app_config_from_stack(stack)
    backend = object()
    calls: list[object] = []

    def resolve(resolved_store):
        calls.append(resolved_store)
        return backend

    monkeypatch.setattr(
        "omop_emb.backends.resolve_backend_from_resolved_vector_store", resolve
    )

    adapters = build_adapters(config)

    assert calls == []
    assert adapters.omop_emb is not None
    assert adapters.omop_emb._get_backend() is backend
    assert adapters.omop_emb._get_backend() is backend
    assert calls == [config.vector_store]


def test_streamable_http_transport_runs_in_stateless_json_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    class FakeFastMCP:
        def __init__(self, name: str, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs

        def tool(self, name=None, description=None):
            return lambda func: func

        def prompt(self, name=None, description=None):
            return lambda func: func

        def resource(self, uri, description=None):
            return lambda func: func

        def run(self, transport: str):
            captured["transport"] = transport

    monkeypatch.setattr("mcp.server.fastmcp.FastMCP", FakeFastMCP)

    server = GroundcrewServer("groundworkers-test")
    server.run(transport="streamable-http", host="0.0.0.0", port=18080)

    assert captured["name"] == "groundworkers-test"
    assert captured["transport"] == "streamable-http"
    assert captured["kwargs"] == {
        "host": "0.0.0.0",
        "port": 18080,
        "json_response": True,
        "stateless_http": True,
    }


def test_stdio_transport_runs_without_json_responses(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class FakeFastMCP:
        def __init__(self, name: str, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs

        def tool(self, name=None, description=None):
            return lambda func: func

        def prompt(self, name=None, description=None):
            return lambda func: func

        def resource(self, uri, description=None):
            return lambda func: func

        def run(self, transport: str):
            captured["transport"] = transport

    monkeypatch.setattr("mcp.server.fastmcp.FastMCP", FakeFastMCP)

    server = GroundcrewServer("groundworkers-test")
    server.run(transport="stdio", host="127.0.0.1", port=18080)

    assert captured["name"] == "groundworkers-test"
    assert captured["transport"] == "stdio"
    assert captured["kwargs"] == {
        "host": "127.0.0.1",
        "port": 18080,
        "json_response": False,
        "stateless_http": True,
    }


def test_parse_args_accepts_rest_transport() -> None:
    args = parse_args(["--transport", "rest", "--port", "8088"])

    assert args.transport == "rest"
    assert args.port == 8088


def test_main_uses_configured_mcp_defaults_when_no_overrides_are_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_app_config_from_stack(
        build_cdm_stack(
            groundworkers={
                "mcp": {
                    "transport": "stdio",
                    "host": "0.0.0.0",
                    "port": 18888,
                }
            }
        )
    )
    fake_application = object()
    captured: dict[str, object] = {}

    class FakeServer:
        def run(self, *, transport: str, host: str, port: int) -> None:
            captured["run"] = {
                "transport": transport,
                "host": host,
                "port": port,
            }

    def fake_build_app_config(*, config_path=None):
        captured["build_app_config"] = {"config_path": config_path}
        return config

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr(
        "groundworkers.server.build_application", lambda _: fake_application
    )
    monkeypatch.setattr(
        "groundworkers.server.create_server", lambda *_args, **_kwargs: FakeServer()
    )

    main([])

    assert captured["build_app_config"] == {"config_path": None}
    assert captured["run"] == {
        "transport": "stdio",
        "host": "0.0.0.0",
        "port": 18888,
    }


def test_main_routes_rest_startup_to_rest_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_app_config_from_stack(
        build_cdm_stack(
            groundworkers={
                "mcp": {
                    "transport": "streamable-http",
                    "host": "127.0.0.1",
                    "port": 18080,
                },
                "rest": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": 18181,
                    "base_path": "/v1",
                },
            }
        )
    )
    fake_application = object()
    captured: dict[str, object] = {}

    class FakeServer:
        def run(self, *, transport: str, host: str, port: int) -> None:
            captured["run"] = {
                "transport": transport,
                "host": host,
                "port": port,
            }

    def fake_build_app_config(*, config_path=None):
        captured["build_app_config"] = {"config_path": config_path}
        return config

    def fake_run_rest_api(app_config, application, *, host: str, port: int) -> None:
        captured["rest"] = {
            "config": app_config,
            "application": application,
            "host": host,
            "port": port,
        }

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr(
        "groundworkers.server.build_application", lambda _: fake_application
    )
    monkeypatch.setattr(
        "groundworkers.server.create_server", lambda *_args, **_kwargs: FakeServer()
    )
    monkeypatch.setattr("groundworkers.server.run_rest_api", fake_run_rest_api)

    main(["--transport", "rest", "--host", "0.0.0.0", "--port", "19090"])

    assert captured["build_app_config"] == {"config_path": None}
    assert captured["rest"] == {
        "config": config,
        "application": fake_application,
        "host": "0.0.0.0",
        "port": 19090,
    }
    assert "run" not in captured


def test_main_launches_setup_tui_without_building_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build_app_config(*, config_path=None):
        raise AssertionError("setup TUI should handle config loading itself")

    def fake_launch_tui(*, config_path=None) -> None:
        captured["launch"] = {"config_path": config_path}

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr(
        "groundworkers.server._launch_groundworkers_tui", fake_launch_tui
    )

    main(["--tui", "--config-path", "/tmp/stack.toml"])

    assert captured["launch"] == {"config_path": "/tmp/stack.toml"}


def test_main_launches_setup_tui_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_launch_tui(*, config_path=None) -> None:
        captured["launch"] = {"config_path": config_path}

    monkeypatch.setattr(
        "groundworkers.server._launch_groundworkers_tui", fake_launch_tui
    )

    main(["tui"])

    assert captured["launch"] == {"config_path": None}


def test_main_launches_legacy_projection_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build_app_config(*, config_path=None):
        raise AssertionError("projection TUI does not need runtime config")

    def fake_launch_tui() -> None:
        captured["launch"] = True

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr(
        "groundworkers.server._launch_semantic_projection_tui", fake_launch_tui
    )

    main(["projection-tui"])

    assert captured["launch"] is True


def test_run_rest_api_uses_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    config = build_app_config_from_stack(_stack_without_backends())
    app = build_application(config)
    captured: dict[str, object] = {}

    def fake_create_rest_app(application, *, base_path: str):
        captured["application"] = application
        captured["base_path"] = base_path
        return object()

    def fake_uvicorn_run(api, *, host: str, port: int):
        captured["api"] = api
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("groundworkers.server.create_rest_app", fake_create_rest_app)
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    run_rest_api(config, app, host="0.0.0.0", port=18181)

    assert captured["application"] is app
    assert captured["base_path"] == config.rest.base_path
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 18181
