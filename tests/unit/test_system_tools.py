from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.tools.system_tools import register_system_tools


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class StubGraphAdapter:
    def __init__(self, *, available: bool = True, detail: str | None = None, emb_active: bool = False):
        self._available = available
        self._detail = detail
        self._emb_active = emb_active

    @property
    def embedding_resolver_active(self) -> bool:
        return self._emb_active

    def probe(self) -> tuple[bool, str | None]:
        return self._available, self._detail

    def get_vocabulary_catalogue(self) -> dict:
        return {"vocabularies": [], "domains": [], "concept_classes": []}


class StubEmbAdapter:
    def __init__(self, *, available: bool = True, backend_type: str = "sqlitevec",
                 model_count: int = 1, has_client: bool = True, raise_on_status: bool = False):
        self._available = available
        self._backend_type = backend_type
        self._model_count = model_count
        self._has_client = has_client
        self._raise_on_status = raise_on_status

    def has_client(self) -> bool:
        return self._has_client

    def index_status(self) -> dict:
        if self._raise_on_status:
            raise RuntimeError("backend unreachable")
        return {
            "available": self._available,
            "backend_type": self._backend_type,
            "models": [{"model_name": f"model-{i}"} for i in range(self._model_count)],
        }


def _server(graph=None, emb=None) -> GroundcrewServer:
    server = GroundcrewServer("test-server")
    register_system_tools(server, graph_adapter=graph, emb_adapter=emb)
    return server


# ---------------------------------------------------------------------------
# system_status — overall field
# ---------------------------------------------------------------------------

def test_system_status_overall_healthy_when_all_available():
    server = _server(
        graph=StubGraphAdapter(available=True),
        emb=StubEmbAdapter(available=True),
    )
    result = server.call("system_status")
    assert result["overall"] == "healthy"


def test_system_status_overall_degraded_when_one_unavailable():
    server = _server(
        graph=StubGraphAdapter(available=True),
        emb=StubEmbAdapter(available=False),
    )
    result = server.call("system_status")
    assert result["overall"] == "degraded"


def test_system_status_overall_unavailable_when_all_down():
    server = _server(
        graph=StubGraphAdapter(available=False),
        emb=StubEmbAdapter(available=False),
    )
    result = server.call("system_status")
    assert result["overall"] == "unavailable"


def test_system_status_overall_unavailable_when_no_adapters_configured():
    server = _server()
    result = server.call("system_status")
    assert result["overall"] == "unavailable"
    assert result["components"] == {}


def test_system_status_overall_healthy_with_only_graph_configured():
    server = _server(graph=StubGraphAdapter(available=True))
    result = server.call("system_status")
    assert result["overall"] == "healthy"


# ---------------------------------------------------------------------------
# system_status — components structure
# ---------------------------------------------------------------------------

def test_system_status_components_key_not_adapters():
    server = _server(graph=StubGraphAdapter())
    result = server.call("system_status")
    assert "components" in result
    assert "adapters" not in result


def test_system_status_omop_graph_component_shape():
    server = _server(graph=StubGraphAdapter(available=True, emb_active=False))
    result = server.call("system_status")
    comp = result["components"]["omop_graph"]
    assert comp["available"] is True
    assert comp["db_connected"] is True
    assert comp["embedding_resolver_active"] is False
    assert comp["detail"] is None


def test_system_status_omop_graph_unavailable_includes_detail():
    server = _server(graph=StubGraphAdapter(available=False, detail="Cannot connect to database: timeout"))
    result = server.call("system_status")
    comp = result["components"]["omop_graph"]
    assert comp["available"] is False
    assert "detail" in comp
    assert comp["detail"] is not None
    assert "connect" in comp["detail"]


def test_system_status_embedding_resolver_active_true_when_wired():
    server = _server(graph=StubGraphAdapter(available=True, emb_active=True))
    result = server.call("system_status")
    assert result["components"]["omop_graph"]["embedding_resolver_active"] is True


def test_system_status_omop_emb_component_shape():
    server = _server(emb=StubEmbAdapter(available=True, backend_type="sqlitevec",
                                        model_count=2, has_client=True))
    result = server.call("system_status")
    comp = result["components"]["omop_emb"]
    assert comp["available"] is True
    assert comp["backend_type"] == "sqlitevec"
    assert comp["model_count"] == 2
    assert comp["client_configured"] is True
    assert comp["detail"] is None


def test_system_status_omop_emb_backend_failure_reflected_in_component():
    server = _server(emb=StubEmbAdapter(raise_on_status=True, has_client=False))
    result = server.call("system_status")
    comp = result["components"]["omop_emb"]
    assert comp["available"] is False
    assert comp["model_count"] == 0
    assert comp["detail"] is not None


def test_system_status_only_configured_adapters_appear_in_components():
    server = _server(graph=StubGraphAdapter())
    result = server.call("system_status")
    assert "omop_graph" in result["components"]
    assert "omop_emb" not in result["components"]


# ---------------------------------------------------------------------------
# system_status — never raises
# ---------------------------------------------------------------------------

def test_system_status_never_raises_regardless_of_adapter_state():
    server = _server(
        graph=StubGraphAdapter(available=False, detail="host unreachable"),
        emb=StubEmbAdapter(raise_on_status=True),
    )
    result = server.call("system_status")
    assert "overall" in result
    assert "components" in result


# ---------------------------------------------------------------------------
# system_vocabulary_catalogue
# ---------------------------------------------------------------------------

def test_vocabulary_catalogue_returns_backend_unavail_when_no_graph():
    server = _server()
    result = server.call("system_vocabulary_catalogue")
    assert result["error"] is True
    assert result["code"] == "BACKEND_UNAVAIL"


def test_vocabulary_catalogue_returns_data_when_graph_configured():
    server = _server(graph=StubGraphAdapter())
    result = server.call("system_vocabulary_catalogue")
    assert "vocabularies" in result
    assert "domains" in result
    assert "concept_classes" in result


def test_vocabulary_catalogue_returns_query_error_on_adapter_exception():
    class FailingGraphAdapter(StubGraphAdapter):
        def get_vocabulary_catalogue(self) -> dict:
            raise RuntimeError("db timeout")

    server = _server(graph=FailingGraphAdapter())
    result = server.call("system_vocabulary_catalogue")
    assert result["error"] is True
    assert result["code"] == "QUERY_ERROR"
