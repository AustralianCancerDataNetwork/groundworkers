from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.app import build_application
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
    assert server.list_tools() == ["system_status", "system_vocabulary_catalogue"]


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


def test_app_config_accepts_all_vocab_sections():
    config = AppConfig.model_validate(
        {
            "database": {"url": "sqlite+pysqlite:///:memory:"},
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
