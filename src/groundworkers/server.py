from __future__ import annotations

import argparse
import json
from groundworkers.app import build_adapters, build_application
from groundworkers.base.server import GroundcrewServer
from groundworkers.config import AppConfig
from groundworkers.tools.concept_tools import register_concept_tools
from groundworkers.tools.embedding_tools import register_embedding_tools
from groundworkers.tools.mapping_tools import register_mapping_tools
from groundworkers.tools.resolver_tools import register_resolver_tools
from groundworkers.tools.search_tools import register_search_tools
from groundworkers.tools.system_tools import register_system_tools
from groundworkers.tools.text_tools import register_text_tools


def create_server(config: AppConfig) -> GroundcrewServer:
    server = GroundcrewServer(config.app_name)
    app = build_application(config)
    server.adapters = app.adapters  # type: ignore[attr-defined]
    server.services = app.services  # type: ignore[attr-defined]
    if app.adapters.omop_graph is not None:
        register_concept_tools(server, app.adapters.omop_graph)
        register_resolver_tools(server, app.adapters.omop_graph)
    if app.adapters.omop_vocab is not None:
        register_search_tools(server, app.adapters.omop_vocab)
        register_mapping_tools(
            server,
            app.services.mapping,
        )
    if app.adapters.omop_emb is not None:
        register_embedding_tools(server, app.adapters.omop_emb)
    if app.services.text is not None:
        register_text_tools(server, app.services.text)
    register_system_tools(server, app.adapters.omop_graph, app.adapters.omop_emb, app.adapters.llm)
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the groundworkers MCP server")
    parser.add_argument("--config", required=True, help="Path to a YAML configuration file")
    parser.add_argument("--describe", action="store_true", help="Print configured tools and exit")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP transport (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port for HTTP transport (default: 8000)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = AppConfig.load(args.config)
    server = create_server(config)
    if args.describe:
        print(json.dumps({"config": config.describe(), "tools": server.describe_tools()}, indent=2))
        return
    server.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
