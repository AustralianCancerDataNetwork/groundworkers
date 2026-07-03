from __future__ import annotations

import argparse
import json

from groundworkers.app import GroundworkersApp, build_adapters, build_application
from groundworkers.base.server import GroundcrewServer
from groundworkers.bootstrap import build_app_config
from groundworkers.config import AppConfig
from groundworkers.tools.concept_tools import register_concept_tools
from groundworkers.tools.domain_tools import register_domain_tools
from groundworkers.tools.embedding_tools import register_embedding_resources, register_embedding_tools
from groundworkers.tools.knowledge_tools import register_knowledge_tools
from groundworkers.tools.mapping_tools import register_mapping_tools
from groundworkers.tools.resolver_tools import register_resolver_tools
from groundworkers.tools.search_tools import register_search_tools
from groundworkers.tools.source_planning_tools import (
    register_source_planning_resources,
    register_source_planning_tools,
)
from groundworkers.tools.system_tools import register_system_resources, register_system_tools
from groundworkers.tools.text_tools import register_text_prompts, register_text_tools
from groundworkers.transports.rest import create_rest_app


def create_server(
    config: AppConfig,
    application: GroundworkersApp | None = None,
) -> GroundcrewServer:
    server = GroundcrewServer(config.app_name)
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
    register_text_prompts(server)
    register_system_tools(server, app.adapters.omop_graph, app.adapters.omop_emb, app.adapters.llm)
    register_system_resources(server, config, app.adapters.omop_graph)
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the groundworkers MCP server")
    parser.add_argument(
        "--config-path",
        help="Path to the shared OMOP stack config TOML. Defaults to OA_CONFIG_PATH or ~/.config/omop/config.toml.",
    )
    parser.add_argument(
        "--profile",
        help="Stack profile override for this process only. Defaults to the stack's active profile.",
    )
    parser.add_argument("--describe", action="store_true", help="Print configured tools and exit")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "rest"],
        help="Transport override. Defaults to tools.groundworkers.mcp.transport for MCP runtimes.",
    )
    parser.add_argument("--host", help="Bind host override for HTTP transports.")
    parser.add_argument("--port", type=int, help="Bind port override for HTTP transports.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = build_app_config(config_path=args.config_path, profile=args.profile)
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
    transport = args.transport or config.mcp.transport
    if transport == "rest":
        run_rest_api(
            config,
            application,
            host=args.host or config.rest.host,
            port=args.port or config.rest.port,
        )
        return
    host = args.host or config.mcp.host
    port = args.port or config.mcp.port
    server.run(transport=transport, host=host, port=port)


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
        base_path=config.rest.base_path,
    )
    uvicorn.run(api, host=host, port=port)


if __name__ == "__main__":
    main()
