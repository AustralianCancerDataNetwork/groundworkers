from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from omop_emb import EmbeddingClient

from groundworkers.adapters.omop_emb import OmopEmbAdapter
from groundworkers.adapters.omop_graph import OmopGraphAdapter
from groundworkers.adapters.omop_vocab import OmopVocabAdapter
from groundworkers.base.server import GroundcrewServer
from groundworkers.base.sql import build_engine
from groundworkers.config import AppConfig
from groundworkers.tools.concept_tools import register_concept_tools
from groundworkers.tools.embedding_tools import register_embedding_tools
from groundworkers.tools.resolver_tools import register_resolver_tools
from groundworkers.tools.search_tools import register_search_tools
from groundworkers.tools.system_tools import register_system_tools


@dataclass
class Adapters:
    omop_graph: OmopGraphAdapter | None = None
    omop_vocab: OmopVocabAdapter | None = None
    omop_emb: OmopEmbAdapter | None = None


def build_adapters(config: AppConfig) -> Adapters:
    adapters = Adapters()

    omop_graph = config.omop_graph
    if omop_graph is not None:
        engine = build_engine(omop_graph.db_url)
        adapters.omop_graph = OmopGraphAdapter(
            engine=engine,
            vocab_schema=omop_graph.vocab_schema,
            emb_model_name=omop_graph.emb_model_name,
        )
        # OmopVocabAdapter shares the same engine — no separate connection pool.
        adapters.omop_vocab = OmopVocabAdapter(engine=engine)

    omop_emb = config.omop_emb
    if omop_emb is not None and omop_emb.enabled:
        cdm_engine = adapters.omop_graph.engine if adapters.omop_graph is not None else None

        def build_backend() -> object:
            backend_type = omop_emb.backend_type.lower()
            if backend_type == "sqlitevec":
                # Requires groundworkers[embedding-sqlitevec] → omop-emb[sqlitevec]
                from omop_emb.backends.sqlitevec import SQLiteVecEmbeddingBackend
                return SQLiteVecEmbeddingBackend.from_path(omop_emb.required_db_path)
            if backend_type == "pgvector":
                # Requires groundworkers[embedding-pgvector] → omop-emb[pgvector]
                from omop_emb.backends.pgvector import PGVectorEmbeddingBackend
                engine = build_engine(omop_emb.required_db_url)
                return PGVectorEmbeddingBackend(emb_engine=engine)
            raise RuntimeError(f"Unsupported embedding backend type: {omop_emb.backend_type}")

        client_factory = None
        api_credentials = omop_emb.configured_api_credentials
        if api_credentials is not None:
            api_base, api_key = api_credentials

            def build_client(model_name: str) -> object:
                return EmbeddingClient(
                    model=model_name,
                    api_base=api_base,
                    api_key=api_key,
                )

            client_factory = build_client

        adapters.omop_emb = OmopEmbAdapter(
            backend_factory=build_backend,
            backend_type=omop_emb.backend_type,
            default_model_name=omop_emb.default_model_name,
            client_factory=client_factory,
            cdm_engine=cdm_engine,
            faiss_cache_dir=omop_emb.faiss_cache_dir,
        )

    return adapters


def create_server(config: AppConfig) -> GroundcrewServer:
    server = GroundcrewServer(config.app_name)
    adapters = build_adapters(config)
    server.adapters = adapters  # type: ignore[attr-defined]
    if adapters.omop_graph is not None:
        register_concept_tools(server, adapters.omop_graph)
        register_resolver_tools(server, adapters.omop_graph)
    if adapters.omop_vocab is not None:
        register_search_tools(server, adapters.omop_vocab)
    if adapters.omop_emb is not None:
        register_embedding_tools(server, adapters.omop_emb)
    register_system_tools(server, adapters.omop_graph, adapters.omop_emb)
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
