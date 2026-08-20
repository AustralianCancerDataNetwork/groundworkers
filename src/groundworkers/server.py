from __future__ import annotations

import argparse
import json
from typing import Literal, cast, get_args

from groundworkers._env import ENV_CONFIG_PATH, rejected_config_path
from groundworkers.app import GroundworkersApp, build_application
from groundworkers.base.server import GroundcrewServer
from groundworkers.bootstrap import build_app_config
from groundworkers.config import AppConfig, GroundworkersConfig
from groundworkers.services.semantic_projection.service import SemanticProjectionService
from groundworkers.tools.concept_tools import register_concept_tools
from groundworkers.tools.domain_tools import register_domain_tools
from groundworkers.tools.embedding_tools import (
    register_embedding_resources,
    register_embedding_tools,
)
from groundworkers.tools.knowledge_tools import register_knowledge_tools
from groundworkers.tools.mapping_tools import register_mapping_tools
from groundworkers.tools.resolver_tools import register_resolver_tools
from groundworkers.tools.search_tools import register_search_tools
from groundworkers.tools.semantic_projection_tools import (
    register_semantic_projection_tools,
)
from groundworkers.tools.source_planning_tools import (
    register_source_planning_resources,
    register_source_planning_tools,
)
from groundworkers.tools.system_tools import (
    register_system_resources,
    register_system_tools,
)
from groundworkers.tools.text_tools import register_text_prompts, register_text_tools
from groundworkers.transports.rest import create_rest_app

# Mirrors GroundcrewServer.run; `rest` is handled before this point and is not
# an MCP transport. mcp_transport is a free-form string, so a value that
# never passed through argparse still has to be validated here.
MCPTransport = Literal["stdio", "sse", "streamable-http"]
_MCP_TRANSPORTS: tuple[str, ...] = get_args(MCPTransport)


def create_server(
    config: AppConfig,
    application: GroundworkersApp | None = None,
) -> GroundcrewServer:
    server = GroundcrewServer(config.groundworkers.app_name)
    app = application or build_application(config)
    if app.services.graph is not None:
        register_concept_tools(server, app.services.graph)
    if app.services.vocab is not None:
        register_search_tools(server, app.services.vocab)
        if app.services.mapping is not None:
            register_mapping_tools(server, app.services.mapping)
    if app.services.grounding is not None:
        register_resolver_tools(server, app.services.grounding)

    if app.adapters.omop_emb is not None:
        register_embedding_tools(server, app.adapters.omop_emb)
        register_embedding_resources(server, app.adapters.omop_emb)
    if app.services.text is not None:
        register_text_tools(server, app.services.text)
    if app.services.domain is not None:
        register_domain_tools(server, app.services.domain)
    if app.services.source_planning is not None:
        register_source_planning_tools(server, app.services.source_planning)
    register_source_planning_resources(server)
    register_knowledge_tools(server, packs_root=config.knowledge_root)
    if config.groundworkers.semantic_projection_enabled:
        register_semantic_projection_tools(server, SemanticProjectionService())
    register_text_prompts(server)
    register_system_tools(
        server,
        app.adapters.omop_graph,
        app.adapters.omop_emb,
        app.adapters.llm,
        embedding_configuration_detail=app.adapters.embedding_configuration_detail,
    )
    register_system_resources(server, config, app.adapters.omop_graph)
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the groundworkers MCP server")
    parser.add_argument(
        "--config-path",
        help="Path to the shared OMOP stack config TOML. Defaults to OA_CONFIG_PATH or ~/.config/omop/config.toml.",
    )
    parser.add_argument(
        "--describe", action="store_true", help="Print configured tools and exit"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v for INFO, -vv for DEBUG). Logs go to stderr, "
        "so they never interfere with the stdio MCP transport.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "rest"],
        help="Transport override. Defaults to tools.groundworkers.mcp_transport for MCP runtimes.",
    )
    parser.add_argument("--host", help="Bind host override for HTTP transports.")
    parser.add_argument(
        "--port", type=int, help="Bind port override for HTTP transports."
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch the interactive Groundworkers setup TUI and exit.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "tui"],
        help="Optional command. Use 'tui' for the setup TUI.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # Configured before the stack is loaded, so oa-configurator's own load-time
    # warnings (a config file with loose permissions, for one) are formatted and
    # redacted rather than falling through to Python's bare last-resort handler.
    # Re-applied with the stack below once its [logging] section is readable;
    # configure_logging is documented as idempotent.
    GroundworkersConfig.configure_logging(verbosity=args.verbose)
    if args.tui or args.command == "tui":
        # The console reports a rejected OA_CONFIG_PATH itself, by opening the
        # location wizard on it, so it is not an error here.
        _launch_groundworkers_tui(config_path=args.config_path)
        return
    if args.config_path is None:
        _require_usable_config_path()
    config = build_app_config(config_path=args.config_path)
    # Now that the stack is loaded, its [logging] section takes precedence, and
    # the namespaces GroundworkersConfig declares (omop_graph, omop_emb) are
    # configured alongside groundworkers' own.
    GroundworkersConfig.configure_logging(config.stack, verbosity=args.verbose)
    application = build_application(config)
    server = create_server(config, application)
    if args.describe:
        print(
            json.dumps(
                {
                    "config": config.describe(),
                    "tools": server.describe_tools(),
                    "prompts": server.describe_prompts(),
                    "resources": server.describe_resources(),
                },
                indent=2,
            )
        )
        return
    transport = args.transport or config.groundworkers.mcp_transport
    if transport == "rest":
        run_rest_api(
            config,
            application,
            host=args.host or config.groundworkers.rest_host,
            port=args.port or config.groundworkers.rest_port,
        )
        return
    if transport not in _MCP_TRANSPORTS:
        raise SystemExit(
            f"Unsupported MCP transport {transport!r}; expected one of "
            f"{', '.join(_MCP_TRANSPORTS)} or 'rest'."
        )
    host = args.host or config.groundworkers.mcp_host
    port = args.port or config.groundworkers.mcp_port
    server.run(transport=cast(MCPTransport, transport), host=host, port=port)


def _require_usable_config_path() -> None:
    """Refuse to serve from a fallback the operator did not ask for.

    ``OA_CONFIG_PATH`` naming a file that is not there used to kill the process
    inside an import, before argparse; it is now dropped early so the console can
    offer to fix it. For a server that leaves the default path in its place,
    which would answer questions from the wrong vocabulary rather than not
    answering them. Say so and stop.
    """

    rejected = rejected_config_path()
    if rejected is None:
        return
    raise SystemExit(
        f"{ENV_CONFIG_PATH} points at {rejected}, which is not an existing .toml "
        "file. Correct it, pass --config-path, or run 'groundworkers tui' to "
        "choose a configuration location."
    )


def _launch_groundworkers_tui(*, config_path: str | None) -> None:
    from groundworkers.tui import run_groundworkers_tui

    run_groundworkers_tui(config_path=config_path)


def run_rest_api(
    config: AppConfig,
    application: GroundworkersApp,
    *,
    host: str,
    port: int,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "REST transport requires FastAPI and uvicorn. Install the project dependencies first."
        ) from exc

    api = create_rest_app(
        application,
        base_path=config.groundworkers.rest_base_path,
    )
    uvicorn.run(api, host=host, port=port)


if __name__ == "__main__":
    main()
