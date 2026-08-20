from pathlib import Path

import pytest

from groundworkers.app import build_adapters, build_application
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.bootstrap import build_app_config_from_stack
from groundworkers.server import create_server, main, parse_args, run_rest_api
from tests.support.stack_config import (
    add_chat_model,
    build_cdm_stack,
    build_embedding_stack,
)


def _stack_without_backends():
    return build_cdm_stack()


def _stack_with_cdm():
    return build_cdm_stack(schema_name="omop", vocab_schema="omop_vocab")


def test_cdm_only_stack_serves_graph_tools_without_embedding_tools():
    """A CDM-only 1.x stack gets vocabulary, graph, and lexical grounding.

    Graph availability follows the resolved CDM database. It is not gated on an
    [tools.omop_graph] section: omop-graph 2.x made that config internal, so
    requiring it left a valid CDM stack with no graph tools at all.
    """
    config = build_app_config_from_stack(_stack_without_backends())
    server = create_server(config)
    names = server.list_tools()
    assert "concept_search_exact" in names
    assert "concept_get" in names
    assert "concept_ground" in names
    # Embedding tools still require a vector store and model.
    assert "embedding_index_status" not in names
    assert "config://active" in server.list_resources()


def test_server_registers_concept_tools_for_a_resolved_cdm_database():
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


def test_build_adapters_leaves_only_embedding_components_unset():
    config = build_app_config_from_stack(_stack_without_backends())
    adapters = build_adapters(config)
    assert adapters.cdm is not None
    # Graph follows the CDM database; embedding needs a vector store and model.
    assert adapters.omop_graph is not None
    assert adapters.omop_graph.embedding_resolver_active is False
    assert adapters.omop_emb is None


def test_build_application_exposes_services_container():
    config = build_app_config_from_stack(_stack_without_backends())
    app = build_application(config)
    assert app.adapters.omop_graph is not None
    assert app.services.graph is not None
    assert app.services.grounding is not None
    assert app.services.mapping is not None
    assert app.services.source_planning is not None


def test_runtime_config_masks_api_keys(tmp_path: Path):
    stack = build_embedding_stack()
    add_chat_model(stack, api_key="chat-secret")
    stack.providers["embedding_provider"].api_key = "emb-secret"
    stack.connections["embedding_main"].database_name = str(tmp_path / "omop_emb.db")
    config = build_app_config_from_stack(stack)

    described = config.describe()

    # Chat and embedding models are the same kind of entry and redact identically.
    assert described["model"]["provider"]["api_key_configured"] is True
    assert described["llm_model"]["provider"]["api_key_configured"] is True
    assert "chat-secret" not in repr(described)
    assert "emb-secret" not in repr(described)


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

    server = GroundworkersMCPServer("groundworkers-test")
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

    server = GroundworkersMCPServer("groundworkers-test")
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
                "mcp_transport": "stdio",
                "mcp_host": "0.0.0.0",
                "mcp_port": 18888,
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
                "mcp_transport": "streamable-http",
                "mcp_host": "127.0.0.1",
                "mcp_port": 18080,
                "rest_enabled": True,
                "rest_host": "127.0.0.1",
                "rest_port": 18181,
                "rest_base_path": "/v1",
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
    assert captured["base_path"] == config.groundworkers.rest_base_path
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 18181


def test_verbosity_flag_is_counted() -> None:
    assert parse_args([]).verbose == 0
    assert parse_args(["-vv"]).verbose == 2


def test_logging_is_configured_before_the_stack_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering matters: oa-configurator warns during load.

    Its loose-file-permissions warning is emitted while reading config.toml, so
    configuring logging afterwards would let it fall through to Python's bare
    last-resort handler, unformatted.
    """
    configured: list[int] = []

    def fake_configure_logging(config=None, *, verbosity=0, **_):
        configured.append(verbosity)

    def fail_to_load(*, config_path=None):
        raise FileNotFoundError("config missing")

    monkeypatch.setattr(
        "groundworkers.config.GroundworkersConfig.configure_logging",
        classmethod(lambda cls, config=None, *, verbosity=0, **kw: configured.append(verbosity)),
    )
    monkeypatch.setattr("groundworkers.server.build_app_config", fail_to_load)

    with pytest.raises(SystemExit, match="groundworkers tui"):
        main(["-vv"])

    assert configured == [2]


def test_logging_reapplies_with_the_stack_once_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second call is what makes the [logging] section take effect."""
    calls: list[object] = []
    config = build_app_config_from_stack(_stack_without_backends())

    monkeypatch.setattr(
        "groundworkers.config.GroundworkersConfig.configure_logging",
        classmethod(lambda cls, cfg=None, *, verbosity=0, **kw: calls.append(cfg)),
    )
    monkeypatch.setattr("groundworkers.server.build_app_config", lambda *, config_path=None: config)
    monkeypatch.setattr("groundworkers.server.build_application", lambda cfg: object())
    monkeypatch.setattr("groundworkers.server.create_server", lambda cfg, app: _DescribeStub())

    main(["--describe"])

    assert calls == [None, config.stack]


class _DescribeStub:
    def describe_tools(self):
        return []

    def describe_prompts(self):
        return []

    def describe_resources(self):
        return []
