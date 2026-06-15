from __future__ import annotations

from groundworkers.adapters.omop_graph import OmopGraphAdapter


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeExecuteResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeKnowledgeGraph:
    def __init__(self, rows):
        self.session = _FakeSession(rows)

    def session_factory(self):
        return self.session


def test_get_domain_root_ids_observation_uses_fallback_query():
    adapter = object.__new__(OmopGraphAdapter)
    adapter._kg = None
    adapter._root_ids_cache = {}
    adapter._DOMAIN_ROOT_CODES = dict(OmopGraphAdapter._DOMAIN_ROOT_CODES)

    fake_kg = _FakeKnowledgeGraph([(111,), (222,), (333,)])
    adapter._get_kg = lambda: fake_kg

    roots = adapter._get_domain_root_ids("Observation")

    assert roots == (111, 222, 333)
    assert len(fake_kg.session.executed) == 1


def test_get_domain_root_ids_known_domain_uses_stable_anchor_lookup():
    adapter = object.__new__(OmopGraphAdapter)
    adapter._kg = None
    adapter._root_ids_cache = {}
    adapter._DOMAIN_ROOT_CODES = dict(OmopGraphAdapter._DOMAIN_ROOT_CODES)

    fake_kg = _FakeKnowledgeGraph([(404684003,)])
    adapter._get_kg = lambda: fake_kg

    roots = adapter._get_domain_root_ids("Condition")

    assert roots == (404684003,)
    assert len(fake_kg.session.executed) == 1
