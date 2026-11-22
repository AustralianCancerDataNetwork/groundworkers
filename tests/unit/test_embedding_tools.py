
from unittest.mock import AsyncMock, Mock

from groundworkers.adapters.omop_emb import CandidateSearchLimits
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.vocab import (
    ConceptMappingResult,
    MappedConcept,
    VocabService,
)
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

    def get_neighbours(
        self,
        concept_id: int,
        limit: int,
        model_name: str | None,
        standard_only: bool = False,
        active_only: bool = True,
    ) -> dict:
        self.calls.append(
            (
                "get_neighbours",
                {
                    "concept_id": concept_id,
                    "limit": limit,
                    "model_name": model_name,
                    "standard_only": standard_only,
                    "active_only": active_only,
                },
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


def build_server(adapter, vocab=None) -> GroundworkersMCPServer:
    server = GroundworkersMCPServer("test-server")
    register_embedding_tools(server, adapter, vocab)
    return server


def test_embedding_neighbours_calls_adapter_with_clamped_limit():
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    result = server.call("embedding_neighbours", concept_id=123, limit=500, model_name=None)

    assert result["query_concept_id"] == 123
    assert adapter.calls == [
        (
            "get_neighbours",
            {
                "concept_id": 123,
                "limit": 50,
                "model_name": None,
                "standard_only": True,
                "active_only": True,
            },
        )
    ]


def test_embedding_neighbours_forwards_concept_filters():
    """standard_only/active_only must reach the adapter: the vector index is partial
    on is_standard AND is_valid, so dropping them silently costs a full scan."""
    adapter = StubEmbAdapter()
    server = build_server(adapter)

    server.call(
        "embedding_neighbours",
        concept_id=123,
        limit=5,
        model_name=None,
        standard_only=True,
        active_only=True,
    )

    assert adapter.calls[0][1]["standard_only"] is True
    assert adapter.calls[0][1]["active_only"] is True


def test_embedding_neighbours_returns_error_dict_for_cava_error():
    class NotFoundAdapter(StubEmbAdapter):
        def get_neighbours(
            self,
            concept_id: int,
            limit: int,
            model_name: str | None,
            standard_only: bool = False,
            active_only: bool = True,
        ) -> dict:
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
        standard_only=False,
        active_only=False,
        model_name="demo-model",
    )

    assert result == {
        "query_text": "hypertension",
        "model_name": "demo-model",
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
    assert adapter.calls == [
        (
            "search",
            {
                "query": "hypertension",
                "limit": 50,
                "domain": "Condition",
                "vocabulary": "SNOMED",
                "standard_only": False,
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
        standard_only=False,
        batch_size=16,
    )

    assert result == {
        "query_texts": ["hypertension", "diabetes"],
        "model_name": "demo-model",
        "results": [[], []],
    }
    assert adapter.calls == [
        (
            "search_batch",
            {
                "queries": ["hypertension", "diabetes"],
                "limit": 50,
                "domain": "Condition",
                "vocabulary": None,
                "standard_only": False,
                "active_only": True,
                "model_name": None,
                "batch_size": 16,
            },
        )
    ]


def test_standard_embedding_search_keeps_cross_domain_targets_without_domain():
    adapter = StubEmbAdapter()
    adapter.async_search = AsyncMock(
        return_value={
            "results": [
                {
                    "concept_id": 123,
                    "concept_name": "Source",
                    "vocabulary_id": "ICD10CM",
                    "similarity": 0.8,
                    "standard_concept": False,
                },
                {
                    "concept_id": 124,
                    "concept_name": "Unmapped",
                    "standard_concept": False,
                },
            ]
        }
    )
    vocab = Mock(spec=VocabService)
    vocab.resolve_domain.return_value = None
    vocab.navigate_to_standard.return_value = [
        ConceptMappingResult(
            123,
            "Source",
            False,
            False,
            [
                MappedConcept(
                    {
                        "concept_id": cid,
                        "concept_name": domain,
                        "domain_id": domain,
                        "standard_concept": True,
                    },
                    "Maps to",
                )
                for cid, domain in (
                    (201, "Condition"),
                    (301, "Measurement"),
                    (401, "Observation"),
                )
            ],
        )
    ]

    result = build_server(adapter, vocab).call(
        "embedding_search", query="source", standard_only=True, limit=2
    )

    assert adapter.async_search.call_args.kwargs["limit"] == CandidateSearchLimits(
        valid=25, standard=2
    )
    assert vocab.navigate_to_standard.call_args.args == ([123, 124],)
    assert [(row["concept_id"], row["domain_id"]) for row in result["results"]] == [
        (201, "Condition"),
        (301, "Measurement"),
    ]
    assert result["results"][0]["reached_via"] == [
        {
            "concept_id": 123,
            "concept_name": "Source",
            "vocabulary_id": "ICD10CM",
            "channel": "embedding",
            "similarity": 0.8,
        }
    ]
    assert "warnings" not in result


def test_standard_embedding_batch_maps_once_and_aligns_each_query_warning():
    first = {
        "concept_id": 123,
        "concept_name": "First source",
        "standard_concept": False,
    }
    second = {
        "concept_id": 124,
        "concept_name": "Second source",
        "standard_concept": False,
    }
    direct = {
        "concept_id": 202,
        "concept_name": "Direct hit",
        "domain_id": "Condition",
        "standard_concept": True,
    }
    adapter = StubEmbAdapter()
    adapter.async_search_batch = AsyncMock(
        return_value={"results": [[first, direct], [second, first]]}
    )
    vocab = Mock(spec=VocabService)
    vocab.resolve_domain.return_value = "Condition"
    vocab.navigate_to_standard.return_value = [
        ConceptMappingResult(
            123, "First source", False, False, [MappedConcept(direct, "Maps to")]
        ),
        ConceptMappingResult(
            124,
            "Second source",
            False,
            False,
            [
                MappedConcept(
                    {
                        "concept_id": 301,
                        "concept_name": "Other target",
                        "domain_id": "Measurement",
                        "standard_concept": True,
                    },
                    "Maps to",
                )
            ],
        ),
    ]

    result = build_server(adapter, vocab).call(
        "embedding_search_batch",
        queries=["first", "second"],
        domain="condition",
        standard_only=True,
    )

    vocab.navigate_to_standard.assert_called_once_with([123, 124])
    assert adapter.async_search_batch.call_args.kwargs["domain"] == "Condition"
    assert adapter.async_search_batch.call_args.kwargs[
        "limit"
    ] == CandidateSearchLimits(valid=25, standard=10)
    assert [[row["concept_id"] for row in group] for group in result["results"]] == [
        [202],
        [202],
    ]
    assert result["warnings"] == [
        "query 2 ('second'): 1 matched concept(s) map to standard concepts outside the Condition domain, not listed above: "
        "Other target (301, Measurement) via 'Second source'",
    ]


def test_standard_embedding_batch_returns_empty_warnings_without_off_domain_hits():
    vocab = Mock(spec=VocabService)
    vocab.resolve_domain.return_value = None
    result = build_server(StubEmbAdapter(), vocab).call(
        "embedding_search_batch", queries=["diabetes"], standard_only=True
    )

    assert result["warnings"] == []
    vocab.navigate_to_standard.assert_not_called()


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
