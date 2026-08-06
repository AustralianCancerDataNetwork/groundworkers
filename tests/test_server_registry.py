from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import logging

import pytest
from oa_configurator import DatabaseConfig, ResourceConfig, StackConfig, ToolConfig

from groundworkers.app import build_application
from groundworkers.base.server import GroundcrewServer
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.server import build_adapters, create_server, main, parse_args, run_rest_api


def _stack_without_backends() -> StackConfig:
    return StackConfig()


def _stack_with_cdm() -> StackConfig:
    return StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
                vocab_schema="omop_vocab",
            )
        },
    )


def test_server_starts_without_domain_tools_when_no_adapters_configured():
    config = build_app_config_from_stack(_stack_without_backends())
    server = create_server(config)
    assert server.list_tools() == [
        "knowledge_catalogue",
        "knowledge_pack",
        "source_plan",
        "source_plan_assisted",
        "system_status",
        "system_vocabulary_catalogue",
    ]
    assert server.list_resources() == [
        "config://active",
        "knowledge://catalogue",
        "source-planning://canonical-headers",
        "source-planning://column-roles",
        "source-planning://ingestion-strategies",
        "vocabularies://catalogue",
    ]


def test_server_registers_concept_tools_when_cdm_resource_is_configured():
    config = build_app_config_from_stack(_stack_with_cdm())
    server = create_server(config)
    names = server.list_tools()
    assert "concept_get" in names
    assert "concept_by_code" in names
    assert "concept_ancestors" in names
    assert "concept_descendants" in names


def test_server_registers_embedding_tools_when_enabled(tmp_path: Path):
    stack = StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
                vocab_schema="omop_vocab",
            )
        },
        tools={
            "omop_emb": ToolConfig(
                extra={
                    "backend": "sqlitevec",
                    "sqlite_path": str(tmp_path / "omop_emb.db"),
                    "embedding_model": "bge-small-en-v1.5",
                }
            )
        },
    )
    config = build_app_config_from_stack(stack)
    server = create_server(config)
    names = server.list_tools()
    assert "embedding_index_status" in names
    assert "embedding_neighbours" in names


def test_build_adapters_leaves_disabled_components_unset():
    config = build_app_config_from_stack(_stack_without_backends())
    adapters = build_adapters(config)
    assert adapters.omop_graph is None
    assert adapters.omop_emb is None


def test_build_application_exposes_services_container():
    config = build_app_config_from_stack(_stack_without_backends())
    app = build_application(config)
    assert app.adapters.omop_graph is None
    assert app.services.mapping is None
    assert app.services.source_planning is not None


def test_runtime_config_masks_api_keys(tmp_path: Path):
    stack = StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
                vocab_schema="omop_vocab",
            )
        },
        tools={
            "groundworkers": ToolConfig(
                extra={
                    "llm": {
                        "enabled": True,
                        "api_base": "http://example.test/v1",
                        "api_key": "secret",
                    }
                }
            ),
            "omop_emb": ToolConfig(
                extra={
                    "backend": "sqlitevec",
                    "sqlite_path": str(tmp_path / "omop_emb.db"),
                    "api_base": "http://embeddings.test/v1",
                    "api_key": "emb-secret",
                    "embedding_model": "bge-small-en-v1.5",
                }
            ),
        }
    )
    config = build_app_config_from_stack(stack)

    described = config.describe()

    assert described["groundworkers"]["llm"]["api_key"] == "***"
    assert described["omop_emb"]["api_key"] == "***"


def test_embedding_wiring_failure_emits_warning(caplog, tmp_path: Path):
    stack = StackConfig(
        databases={
            "cdm": DatabaseConfig(
                dialect="sqlite",
                database_name=":memory:",
            )
        },
        resources={
            "cdm_db": ResourceConfig(
                database="cdm",
                cdm_schema="omop",
                vocab_schema="omop_vocab",
            )
        },
        tools={
            "omop_emb": ToolConfig(
                extra={
                    "backend": "sqlitevec",
                    "sqlite_path": str(tmp_path / "emb.db"),
                    "api_base": "http://localhost:9999/v1",
                    "api_key": "test-key",
                    "embedding_model": "bge-small-en-v1.5",
                }
            )
        },
    )
    config = build_app_config_from_stack(stack)
    with caplog.at_level(logging.WARNING, logger="groundworkers.app"):
        build_adapters(config)

    assert any("embedding tier" in r.message for r in caplog.records)


def test_streamable_http_transport_runs_in_stateless_json_mode(monkeypatch: pytest.MonkeyPatch):
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
        StackConfig(
            tools={
                "groundworkers": ToolConfig(
                    extra={
                        "mcp": {
                            "transport": "stdio",
                            "host": "0.0.0.0",
                            "port": 18888,
                        }
                    }
                )
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

    def fake_build_app_config(*, config_path=None, profile=None):
        captured["build_app_config"] = {
            "config_path": config_path,
            "profile": profile,
        }
        return config

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr("groundworkers.server.build_application", lambda _: fake_application)
    monkeypatch.setattr("groundworkers.server.create_server", lambda *_args, **_kwargs: FakeServer())

    main([])

    assert captured["build_app_config"] == {
        "config_path": None,
        "profile": None,
    }
    assert captured["run"] == {
        "transport": "stdio",
        "host": "0.0.0.0",
        "port": 18888,
    }


def test_main_routes_profiled_rest_startup_to_rest_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    config = build_app_config_from_stack(
        StackConfig(
            tools={
                "groundworkers": ToolConfig(
                    extra={
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

    def fake_build_app_config(*, config_path=None, profile=None):
        captured["build_app_config"] = {
            "config_path": config_path,
            "profile": profile,
        }
        return config

    def fake_run_rest_api(app_config, application, *, host: str, port: int) -> None:
        captured["rest"] = {
            "config": app_config,
            "application": application,
            "host": host,
            "port": port,
        }

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr("groundworkers.server.build_application", lambda _: fake_application)
    monkeypatch.setattr("groundworkers.server.create_server", lambda *_args, **_kwargs: FakeServer())
    monkeypatch.setattr("groundworkers.server.run_rest_api", fake_run_rest_api)

    main(["--profile", "test", "--transport", "rest", "--host", "0.0.0.0", "--port", "19090"])

    assert captured["build_app_config"] == {
        "config_path": None,
        "profile": "test",
    }
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

    def fake_build_app_config(*, config_path=None, profile=None):
        raise AssertionError("setup TUI should handle config loading itself")

    def fake_launch_tui(*, config_path=None, profile=None) -> None:
        captured["launch"] = {
            "config_path": config_path,
            "profile": profile,
        }

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr("groundworkers.server._launch_groundworkers_tui", fake_launch_tui)

    main(["--tui", "--config-path", "/tmp/stack.toml", "--profile", "test"])

    assert captured["launch"] == {
        "config_path": "/tmp/stack.toml",
        "profile": "test",
    }


def test_main_launches_setup_tui_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_launch_tui(*, config_path=None, profile=None) -> None:
        captured["launch"] = {
            "config_path": config_path,
            "profile": profile,
        }

    monkeypatch.setattr("groundworkers.server._launch_groundworkers_tui", fake_launch_tui)

    main(["tui", "--profile", "test"])

    assert captured["launch"] == {
        "config_path": None,
        "profile": "test",
    }


def test_main_launches_legacy_projection_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build_app_config(*, config_path=None, profile=None):
        raise AssertionError("projection TUI does not need runtime config")

    def fake_launch_tui() -> None:
        captured["launch"] = True

    monkeypatch.setattr("groundworkers.server.build_app_config", fake_build_app_config)
    monkeypatch.setattr("groundworkers.server._launch_semantic_projection_tui", fake_launch_tui)

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
