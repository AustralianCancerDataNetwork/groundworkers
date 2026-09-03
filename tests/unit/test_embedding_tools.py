

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.tools.embedding_tools import register_embedding_tools


class StubEmbAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def index_status(self) -> dict:
        self.calls.append(("index_status", {}))
        return {
            "available": True,
            "backend_type": "sqlitevec",
            "models": [
                {
                    "model_name": "demo-model",
                    "provider": "ollama",
                    "dimensions": 384,
                    "index_type": "flat",
                    "concept_count": 10,
                }
            ],
        }

    def get_neighbours(self, concept_id: int, limit: int, model_name: str | None) -> dict:
        self.calls.append(
            (
                "get_neighbours",
                {"concept_id": concept_id, "limit": limit, "model_name": model_name},
            )
        )
        return {
            "query_concept_id": concept_id,
            "model_name": model_name or "demo-model",
            "results": [],
        }

    def search(
        self,
        query: str,
        limit: int,
        domain: str | None,
        vocabulary: str | None,
        standard_only: bool,
        active_only: bool,
        model_name: str | None,
    ) -> dict:
        self.calls.append(
            (
                "search",
                {
                    "query": query,
                    "limit": limit,
                    "domain": domain,
                    "vocabulary": vocabulary,
                    "standard_only": standard_only,
                    "active_only": active_only,
                    "model_name": model_name,
                },
            )
        )
        return {
            "query_text": query,
            "model_name": model_name or "demo-model",
            "results": [
                {
                    "concept_id": 123,
                    "concept_name": "Demo concept",
                    "similarity": 0.9,
                    "is_standard": True,
                    "is_active": True,
                }
            ],
        }

    def encode(self, text: str, model_name: str | None) -> dict:
        self.calls.append(("encode", {"text": text, "model_name": model_name}))
        return {
            "text": text,
            "model_name": model_name or "demo-model",
            "dimensions": 3,
            "vector": [0.1, 0.2, 0.3],
        }

    async def async_search(self, **kwargs) -> dict:
        return self.search(**kwargs)

    async def async_search_batch(self, **kwargs) -> dict:
        self.calls.append(("search_batch", kwargs))
        return {
            "query_texts": kwargs["queries"],
            "model_name": kwargs["model_name"] or "demo-model",
            "results": [[] for _ in kwargs["queries"]],
        }

    async def async_encode(self, text: str, model_name: str | None) -> dict:
        return self.encode(text=text, model_name=model_name)


def build_server(adapter) -> GroundworkersMCPServer:
    server = GroundworkersMCPServer("test-server")
    register_embedding_tools(server, adapter)
    return server


def test_embedding_neighbours_calls_adapter_with_clamped_limit():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_neighbours", concept_id=123, limit=500, model_name=None)

    assert result["query_concept_id"] == 123
    assert adapter.calls == [
        ("get_neighbours", {"concept_id": 123, "limit": 50, "model_name": None})
    ]


def test_embedding_neighbours_returns_error_dict_for_cava_error():
    class NotFoundAdapter(StubEmbAdapter):
        def get_neighbours(self, concept_id: int, limit: int, model_name: str | None) -> dict:
            raise GroundworkersError("NOT_FOUND", "missing concept")

    server = build_server(NotFoundAdapter())

    result = server.call("embedding_neighbours", concept_id=999, limit=5, model_name=None)

    assert result == {"error": True, "code": "NOT_FOUND", "message": "missing concept"}


def test_embedding_index_status_returns_adapter_result():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_index_status")

    assert result["available"] is True
    assert result["models"][0]["model_name"] == "demo-model"
    assert adapter.calls == [("index_status", {})]


def test_embedding_neighbours_rejects_invalid_concept_id():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_neighbours", concept_id=0, limit=10, model_name=None)

    assert result == {
        "error": True,
        "code": "INVALID_INPUT",
        "message": "concept_id must be a positive integer",
    }
    assert adapter.calls == []


def test_embedding_search_calls_adapter_with_clamped_limit_and_filters():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call(
        "embedding_search",
        query="hypertension",
        limit=500,
        domain="Condition",
        vocabulary="SNOMED",
        standard_only=True,
        active_only=False,
        model_name="demo-model",
    )

    assert result["query_text"] == "hypertension"
    assert adapter.calls == [
        (
            "search",
            {
                "query": "hypertension",
                "limit": 50,
                "domain": "Condition",
                "vocabulary": "SNOMED",
                "standard_only": True,
                "active_only": False,
                "model_name": "demo-model",
            },
        )
    ]


def test_embedding_search_rejects_empty_query():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_search", query="   ")

    assert result == {
        "error": True,
        "code": "INVALID_INPUT",
        "message": "query must be a non-empty string",
    }
    assert adapter.calls == []


def test_embedding_search_batch_passes_shared_filters_and_batch_size():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call(
        "embedding_search_batch",
        queries=["hypertension", "diabetes"],
        limit=500,
        domain="Condition",
        standard_only=True,
        batch_size=16,
    )

    assert result["query_texts"] == ["hypertension", "diabetes"]
    assert adapter.calls == [
        (
            "search_batch",
            {
                "queries": ["hypertension", "diabetes"],
                "limit": 50,
                "domain": "Condition",
                "vocabulary": None,
                "standard_only": True,
                "active_only": True,
                "model_name": None,
                "batch_size": 16,
            },
        )
    ]


def test_embedding_search_batch_rejects_empty_query():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_search_batch", queries=["", "diabetes"])

    assert result == {
        "error": True,
        "code": "INVALID_INPUT",
        "message": "queries must not contain empty strings",
    }
    assert adapter.calls == []


def test_embedding_search_returns_error_dict_for_cava_error():
    class UnavailableAdapter(StubEmbAdapter):
        async def async_search(
            self,
            query: str,
            limit: int,
            domain: str | None,
            vocabulary: str | None,
            standard_only: bool,
            active_only: bool,
            model_name: str | None,
        ) -> dict:
            raise GroundworkersError("BACKEND_UNAVAIL", "no client configured")

    server = build_server(UnavailableAdapter())

    result = server.call("embedding_search", query="hypertension")

    assert result == {"error": True, "code": "BACKEND_UNAVAIL", "message": "no client configured"}


def test_embedding_encode_returns_adapter_result():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_encode", text="diabetes mellitus", model_name=None)

    assert result["dimensions"] == 3
    assert adapter.calls == [("encode", {"text": "diabetes mellitus", "model_name": None})]


def test_embedding_encode_rejects_empty_text():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_encode", text="   ", model_name=None)

    assert result == {
        "error": True,
        "code": "INVALID_INPUT",
        "message": "text must be a non-empty string",
    }
    assert adapter.calls == []
