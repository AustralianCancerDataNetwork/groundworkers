

from groundworkers.base.server import GroundworkersMCPServer
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
                 model_count: int = 1, has_model_backend: bool = True,
                 raise_on_status: bool = False, detail: str | None = None,
                 live_available: bool = True):
        self._available = available
        self._backend_type = backend_type
        self._model_count = model_count
        self._has_model_backend = has_model_backend
        self._raise_on_status = raise_on_status
        self._detail = detail
        self._live_available = live_available

    def has_model_backend(self) -> bool:
        return self._has_model_backend

    def probe_live_query(self) -> tuple[bool, str | None]:
        return (
            (True, None)
            if self._live_available
            else (False, "embedding smoke probe failed")
        )

    def index_status(self) -> dict:
        if self._raise_on_status:
            raise RuntimeError("backend unreachable")
        result: dict = {
            "available": self._available,
            "backend_type": self._backend_type,
            "models": [{"model_name": f"model-{i}"} for i in range(self._model_count)],
        }
        if self._detail is not None:
            result["detail"] = self._detail
        return result


class StubLLMAdapter:
    def __init__(self, *, available: bool = True, provider: str = "openai-compatible",
                 default_model: str | None = "test-model", detail: str | None = None,
                 raise_on_status: bool = False):
        self._available = available
        self._provider = provider
        self._default_model = default_model
        self._detail = detail
        self._raise_on_status = raise_on_status

    def status(self) -> dict:
        if self._raise_on_status:
            raise RuntimeError("LLM API unreachable")
        result: dict = {
            "available": self._available,
            "provider": self._provider,
            "default_model": self._default_model,
            "structured_output_supported": True if self._available else None,
        }
        if self._detail is not None:
            result["detail"] = self._detail
        return result


class StubVocabService:
    def __init__(self, *, live_available: bool = True) -> None:
        self._live_available = live_available
        self.calls = 0

    def probe_fulltext(self) -> tuple[bool, str | None]:
        self.calls += 1
        return (
            (True, None)
            if self._live_available
            else (False, "full-text smoke probe failed")
        )


def _server(
    graph=None,
    emb=None,
    llm=None,
    embedding_configuration_detail: str | None = None,
    vocab=None,
) -> GroundworkersMCPServer:
    server = GroundworkersMCPServer("test-server")
    register_system_tools(
        server,
        graph_adapter=graph,
        emb_adapter=emb,
        llm_adapter=llm,
        embedding_configuration_detail=embedding_configuration_detail,
        vocab_service=vocab,
    )
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
                                        model_count=2, has_model_backend=True))
    result = server.call("system_status")
    comp = result["components"]["omop_emb"]
    assert comp["available"] is True
    assert comp["backend_type"] == "sqlitevec"
    assert comp["model_count"] == 2
    assert comp["model_backend_configured"] is True
    assert comp["detail"] is None


def test_system_status_reports_live_embedding_failure_separately_from_store():
    server = _server(emb=StubEmbAdapter(live_available=False))

    result = server.call("system_status")

    comp = result["components"]["omop_emb"]
    assert result["overall"] == "unavailable"
    assert comp["available"] is False
    assert comp["store_available"] is True
    assert comp["live_query_available"] is False
    assert "smoke probe failed" in comp["detail"]


def test_system_status_reports_fulltext_smoke_failure():
    server = _server(
        graph=StubGraphAdapter(),
        vocab=StubVocabService(live_available=False),
    )

    result = server.call("system_status")

    comp = result["components"]["omop_graph"]
    assert result["overall"] == "unavailable"
    assert comp["available"] is False
    assert comp["db_connected"] is True
    assert comp["fulltext_available"] is False
    assert "smoke probe failed" in comp["detail"]


def test_system_status_caches_live_probes_briefly():
    vocab = StubVocabService()
    server = _server(graph=StubGraphAdapter(), vocab=vocab)

    server.call("system_status")
    server.call("system_status")

    assert vocab.calls == 1


def test_system_status_omop_emb_backend_failure_reflected_in_component():
    server = _server(
        emb=StubEmbAdapter(raise_on_status=True, has_model_backend=False)
    )
    result = server.call("system_status")
    comp = result["components"]["omop_emb"]
    assert comp["available"] is False
    assert comp["model_count"] == 0
    assert comp["detail"] is not None


def test_system_status_omop_emb_detail_propagated_from_index_status():
    # Mirrors the real OmopEmbAdapter behaviour: index_status never raises but
    # includes a 'detail' field when the backend is unreachable.
    server = _server(emb=StubEmbAdapter(
        available=False, model_count=0, has_model_backend=False,
        detail="GroundworkersError('BACKEND_UNAVAIL', 'Embedding backend is unavailable: ...')",
    ))
    result = server.call("system_status")
    comp = result["components"]["omop_emb"]
    assert comp["available"] is False
    assert comp["detail"] is not None
    assert "BACKEND_UNAVAIL" in comp["detail"]


def test_system_status_reports_model_without_vector_store():
    server = _server(
        embedding_configuration_detail=(
            "The embedding model is configured without a vector store."
        )
    )

    result = server.call("system_status")

    assert result["overall"] == "unavailable"
    assert result["components"]["omop_emb"] == {
        "available": False,
        "backend_type": None,
        "model_count": 0,
        "model_backend_configured": True,
        "store_available": False,
        "live_query_available": None,
        "detail": "The embedding model is configured without a vector store.",
    }


# ---------------------------------------------------------------------------
# system_status — llm component
# ---------------------------------------------------------------------------

def test_system_status_llm_component_shape():
    server = _server(llm=StubLLMAdapter(available=True, provider="openai-compatible",
                                        default_model="gpt-4o-mini"))
    result = server.call("system_status")
    comp = result["components"]["llm"]
    assert comp["available"] is True
    assert comp["provider"] == "openai-compatible"
    assert comp["default_model"] == "gpt-4o-mini"
    assert comp["structured_output_supported"] is True
    assert comp["detail"] is None


def test_system_status_llm_unavailable_includes_detail():
    server = _server(llm=StubLLMAdapter(
        available=False, detail="ConnectionError('refused')",
    ))
    result = server.call("system_status")
    comp = result["components"]["llm"]
    assert comp["available"] is False
    assert comp["detail"] is not None
    assert "refused" in comp["detail"]


def test_system_status_llm_does_not_appear_when_not_configured():
    server = _server(graph=StubGraphAdapter())
    result = server.call("system_status")
    assert "llm" not in result["components"]


def test_system_status_overall_degraded_when_llm_unavailable_and_graph_available():
    server = _server(
        graph=StubGraphAdapter(available=True),
        llm=StubLLMAdapter(available=False),
    )
    result = server.call("system_status")
    assert result["overall"] == "degraded"


def test_system_status_overall_healthy_when_llm_and_graph_both_available():
    server = _server(
        graph=StubGraphAdapter(available=True),
        llm=StubLLMAdapter(available=True),
    )
    result = server.call("system_status")
    assert result["overall"] == "healthy"


def test_system_status_llm_status_exception_still_returns_component():
    server = _server(llm=StubLLMAdapter(raise_on_status=True))
    result = server.call("system_status")
    comp = result["components"]["llm"]
    assert comp["available"] is False
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


def test_vocabulary_catalogue_returns_internal_error_on_adapter_exception():
    class FailingGraphAdapter(StubGraphAdapter):
        def get_vocabulary_catalogue(self) -> dict:
            raise RuntimeError("db timeout")

    server = _server(graph=FailingGraphAdapter())
    result = server.call("system_vocabulary_catalogue")
    assert result["error"] is True
    assert result["code"] == "INTERNAL_ERROR"
    assert "Incident ID" in result["message"]
