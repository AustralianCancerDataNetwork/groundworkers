from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import logging
import pytest
from pydantic import ValidationError

from groundworkers.app import build_application
from groundworkers.base.server import GroundcrewServer
from groundworkers.config import AppConfig
from groundworkers.server import build_adapters, create_server


def test_server_starts_without_domain_tools_when_no_adapters_configured():
    config = AppConfig.model_validate(
        {
            "omop_emb": {"enabled": False},
        }
    )
    server = create_server(config)
    # system_status and system_vocabulary_catalogue are always registered
    # regardless of adapter availability so clients can always query availability.
    assert server.list_tools() == ["knowledge_catalogue", "source_plan", "source_plan_assisted", "system_status", "system_vocabulary_catalogue"]
    assert server.list_resources() == [
        "config://active",
        "knowledge://catalogue",
        "source-planning://canonical-headers",
        "source-planning://column-roles",
        "source-planning://ingestion-strategies",
        "vocabularies://catalogue",
    ]


def test_server_registers_concept_tools_when_omop_graph_is_configured():
    config = AppConfig.model_validate(
        {
            "omop_graph": {
                "db_url": "sqlite+pysqlite:///:memory:",
            }
        }
    )
    server = create_server(config)
    names = server.list_tools()
    assert "concept_get" in names
    assert "concept_by_code" in names
    assert "concept_ancestors" in names
    assert "concept_descendants" in names


def test_server_registers_embedding_tools_when_enabled():
    config = AppConfig.model_validate(
        {
            "omop_emb": {
                "enabled": True,
                "backend_type": "sqlitevec",
                "db_path": "/tmp/omop_emb.db",
            }
        }
    )
    server = create_server(config)
    names = server.list_tools()
    assert "embedding_index_status" in names
    assert "embedding_neighbours" in names


def test_build_adapters_leaves_disabled_components_unset():
    config = AppConfig.model_validate(
        {
            "omop_emb": {"enabled": False},
        }
    )
    adapters = build_adapters(config)
    assert adapters.omop_graph is None
    assert adapters.omop_emb is None


def test_build_application_exposes_services_container():
    config = AppConfig.model_validate(
        {
            "omop_emb": {"enabled": False},
        }
    )
    app = build_application(config)
    assert app.adapters.omop_graph is None
    assert app.services.mapping is None
    assert app.services.source_planning is not None


def test_app_config_accepts_all_vocab_sections():
    config = AppConfig.model_validate(
        {
            "omop_graph": {
                "db_url": "sqlite+pysqlite:///:memory:",
                "vocab_schema": "omop_vocab",
            },
            "omop_emb": {
                "enabled": False,
                "backend_type": "sqlitevec",
                "default_model_name": "bge-small-en-v1.5",
            },
        }
    )
    assert config.omop_graph is not None
    assert config.omop_graph.vocab_schema == "omop_vocab"
    assert config.omop_emb is not None


def test_omop_emb_config_accepts_faiss_backend_type():
    # faiss is not rejected at config time — the limitation surfaces at runtime
    # when the backend factory is invoked, with a clear error message.
    config = AppConfig.model_validate(
        {"omop_emb": {"enabled": False, "backend_type": "faiss", "faiss_cache_dir": "/tmp/faiss"}}
    )
    assert config.omop_emb is not None
    assert config.omop_emb.backend_type == "faiss"


def test_omop_emb_config_accepts_pgvector():
    config = AppConfig.model_validate(
        {"omop_emb": {"enabled": False, "backend_type": "pgvector"}}
    )
    assert config.omop_emb is not None
    assert config.omop_emb.backend_type == "pgvector"


def test_embedding_wiring_failure_emits_warning(caplog):
    config = AppConfig.model_validate(
        {
            "omop_graph": {"db_url": "sqlite+pysqlite:///:memory:"},
            "omop_emb": {
                "enabled": True,
                "backend_type": "sqlitevec",
                "db_path": "/nonexistent/path/emb.db",
                "api_base": "http://localhost:9999",
                "api_key": "test-key",
            },
        }
    )
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
