from __future__ import annotations

from types import SimpleNamespace

from groundworkers.adapters.omop_graph import OmopGraphAdapter


def test_set_embedding_client_invalidates_cached_knowledge_graph():
    adapter = object.__new__(OmopGraphAdapter)
    adapter._kg = object()
    adapter.emb_model_name = "old-model"
    adapter._embedding_client = None

    client = SimpleNamespace(name="embedding-client")

    adapter.set_embedding_client(client, model_name="new-model")

    assert adapter._embedding_client is client
    assert adapter.emb_model_name == "new-model"
    assert adapter._kg is None


def test_wrap_graph_error_does_not_special_case_parentless_not_implemented():
    wrapped = OmopGraphAdapter._wrap_graph_error(
        NotImplementedError("Grounding without parent_ids is not supported."),
        default_code="QUERY_ERROR",
    )

    assert wrapped.code == "QUERY_ERROR"
    assert wrapped.message == "Grounding without parent_ids is not supported."
