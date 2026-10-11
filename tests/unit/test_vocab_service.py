from __future__ import annotations

import re
from datetime import date

import pytest
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
    Concept_Synonym,
)
from sqlalchemy import column as sa_column
from sqlalchemy import create_engine, event

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.base.sql import normalization_expression
from groundworkers.services.vocab import VocabService, normalize_text_for_matching
from groundworkers.tools.search_tools import register_search_tools
from tests.support.omop import route_sqlite_omop_roles


def _service(tmp_path) -> VocabService:
    engine = route_sqlite_omop_roles(
        create_engine(f"sqlite:///{tmp_path / 'vocabulary.db'}")
    )

    @event.listens_for(engine, "connect")
    def sqlite_normalisation_functions(connection, _record) -> None:
        def regexp_replace(value, pattern, replacement, _flags):
            pattern = pattern.replace(r"\m", r"\b").replace(r"\M", r"\b")
            return re.sub(pattern, replacement, value or "")

        connection.create_function("regexp_replace", 4, regexp_replace)
        connection.create_function("btrim", 1, lambda value: (value or "").strip())

    for table in (
        Concept.__table__,
        Concept_Synonym.__table__,
        Concept_Relationship.__table__,
        Concept_Ancestor.__table__,
    ):
        table.create(engine)

    with engine.begin() as connection:
        connection.execute(
            Concept.__table__.insert(),
            (
                _concept(1, "Diabetes mellitus", "SNOMED", "S"),
                _concept(2, "Diabetes mellitus, unspecified", "ICD10CM", None),
                _concept(3, "Retired diabetes", "SNOMED", "S", invalid_reason="D"),
            ),
        )
        connection.execute(
            Concept_Synonym.__table__.insert(),
            (
                {
                    "concept_id": 1,
                    "concept_synonym_name": "Diabetes",
                    "language_concept_id": 0,
                },
            ),
        )
        connection.execute(
            Concept_Relationship.__table__.insert(),
            (
                {
                    "concept_id_1": 2,
                    "concept_id_2": 1,
                    "relationship_id": "Maps to",
                    "valid_start_date": date(2000, 1, 1),
                    "valid_end_date": date(2099, 12, 31),
                    "invalid_reason": None,
                },
            ),
        )
    return VocabService(CDMAdapter(engine))


def _concept(
    concept_id: int,
    name: str,
    vocabulary: str,
    standard: str | None,
    *,
    invalid_reason: str | None = None,
) -> dict[str, object]:
    return {
        "concept_id": concept_id,
        "concept_name": name,
        "domain_id": "Condition",
        "vocabulary_id": vocabulary,
        "concept_class_id": "Clinical Finding",
        "standard_concept": standard,
        "concept_code": str(concept_id),
        "valid_start_date": date(2000, 1, 1),
        "valid_end_date": date(2099, 12, 31),
        "invalid_reason": invalid_reason,
    }


def test_vocab_service_exact_normalized_sidecar_and_standard_navigation(
    tmp_path,
) -> None:
    service = _service(tmp_path)

    exact = service.search_exact("diabetes", include_synonyms=True)
    normalized = service.search_normalized(
        "DIABETES MELLITUS",
        vocabulary_id="ICD10CM",
    )
    fulltext, available = service.search_fulltext("diabetes")
    navigation = service.navigate_to_standard([1, 2, 999])

    assert [(item.concept_id, item.match_source) for item in exact] == [(1, "synonym")]
    assert [item.concept_id for item in normalized] == [2]
    assert fulltext == []
    assert available is False
    assert service.fts_available is False
    assert [item.source_concept_id for item in navigation] == [1, 2]
    # A standard source maps to itself, marked with the "self" sentinel.
    assert navigation[0].targets[0].relationship_id == "self"
    assert navigation[1].targets[0].concept["concept_id"] == 1
    # Targets now carry the full flag set, so a caller can tell a standard
    # target from a classification one without a second lookup.
    target = navigation[1].targets[0].concept
    assert target["standard_concept"] is True
    assert target["classification_concept"] is False
    assert "concept_code" in target and "is_active" in target


def test_fulltext_probe_uses_the_first_available_concept_label(
    tmp_path, monkeypatch
) -> None:
    service = _service(tmp_path)
    queried: list[str] = []

    def fake_search(query: str, **_kwargs):
        queried.append(query)
        return [object()], True

    monkeypatch.setattr(service, "search_fulltext", fake_search)

    assert service.probe_fulltext() == (True, None)
    assert queried == ["Diabetes mellitus"]


def test_search_tools_expose_the_characterized_vocab_service(tmp_path) -> None:
    server = GroundworkersMCPServer("search-test")
    register_search_tools(server, _service(tmp_path))

    exact = server.call("concept_search_exact", query="diabetes")
    fulltext = server.call("concept_search_fulltext", query="diabetes")
    navigation = server.call("concept_navigate_to_standard", concept_ids=[2])

    assert exact["results"][0]["concept_id"] == 1
    assert exact["results"][0]["matched_synonym"] == "Diabetes"
    assert fulltext == {
        "query": "diabetes",
        "tsvector_available": False,
        "results": [],
    }
    assert navigation["results"][0]["standard_concepts"][0]["concept_id"] == 1


# ---------------------------------------------------------------------------
# Normalized-search affordability guard
# ---------------------------------------------------------------------------


def test_normalized_search_is_refused_when_unscoped_and_unindexed(
    tmp_path,
) -> None:
    """No index and no scope requires the caller to choose a remedy."""
    service = _service(tmp_path)

    with pytest.raises(GroundworkersError) as excinfo:
        service.search_normalized("diabetes mellitus")

    assert "Setup → Performance → normalised-name indexes" in excinfo.value.message
    assert "domain, vocabulary_id, or parent_ids" in excinfo.value.message


def test_empty_parent_ids_do_not_count_as_a_scope(tmp_path) -> None:
    service = _service(tmp_path)

    with pytest.raises(GroundworkersError) as excinfo:
        service.search_normalized("diabetes mellitus", parent_ids=[])

    assert excinfo.value.code == "INVALID_INPUT"


def test_a_domain_filter_allows_a_scoped_scan(tmp_path) -> None:
    service = _service(tmp_path)

    results = service.search_normalized("diabetes mellitus", domain="Condition")
    assert [item.concept_id for item in results] == [1, 2]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"vocabulary_id": "ICD10CM"},
        {"parent_ids": [1]},
        {"domain": "Condition"},
        {"allow_unindexed_scan": True},
    ],
    ids=["vocabulary_id", "parent_ids", "domain", "explicit_opt_in"],
)
def test_normalized_search_runs_when_bounded_or_explicitly_allowed(
    tmp_path, kwargs
) -> None:
    service = _service(tmp_path)

    # Any of these makes the call affordable or accepts the cost deliberately.
    assert isinstance(service.search_normalized("diabetes mellitus", **kwargs), list)


def test_normalized_search_plan_uses_index_without_filters(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service._cdm.engine.dialect, "name", "postgresql")
    monkeypatch.setattr(service, "_has_normalized_index", lambda *_args: True)

    assert service._normalized_search_plan(
        domain=None,
        vocabulary_id=None,
        parent_ids=None,
        include_synonyms=False,
        allow_unindexed_scan=False,
    ) == "indexed"


def test_normalized_search_runs_unfiltered_when_index_is_available(
    tmp_path, monkeypatch
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_normalized_search_plan",
        lambda **_kwargs: "indexed",
    )

    results, plan = service.search_normalized_with_plan("diabetes mellitus")

    assert plan == "indexed"
    assert [item.concept_id for item in results] == [1, 2]


def test_normalized_search_translates_postgres_statement_timeout(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)

    class QueryCanceled(RuntimeError):
        sqlstate = "57014"

    class FakeSession:
        calls = 0
        bind_arguments = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _statement, **kwargs):
            self.calls += 1
            if self.calls == 1:
                type(self).bind_arguments = kwargs.get("bind_arguments")
                return None  # SET LOCAL statement_timeout
            raise QueryCanceled("canceling statement due to statement timeout")

    monkeypatch.setattr(service._cdm.engine.dialect, "name", "postgresql")
    monkeypatch.setattr(service, "_has_normalized_index", lambda *_args: True)
    monkeypatch.setattr(service._cdm, "session", FakeSession)

    with pytest.raises(GroundworkersError) as excinfo:
        service.search_normalized("diabetes", domain="Condition")

    assert excinfo.value.code == "QUERY_TIMEOUT"
    assert "normalised-name indexes" in excinfo.value.message
    assert "domain, vocabulary_id, or parent_ids" in excinfo.value.message
    assert "15 second time limit" in excinfo.value.message
    assert FakeSession.bind_arguments == {"mapper": Concept}


def test_explicit_unindexed_scan_skips_postgres_statement_timeout(
    tmp_path, monkeypatch
) -> None:
    service = _service(tmp_path)
    statements = []

    class FakeResult:
        def all(self):
            return []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            statements.append(statement)
            return FakeResult()

    monkeypatch.setattr(service._cdm.engine.dialect, "name", "postgresql")
    monkeypatch.setattr(service, "_has_normalized_index", lambda *_args: False)
    monkeypatch.setattr(service._cdm, "session", FakeSession)

    results, plan = service.search_normalized_with_plan(
        "diabetes", allow_unindexed_scan=True
    )

    assert plan == "unindexed_scan"
    assert results == []
    assert len(statements) == 1
    assert "SET LOCAL statement_timeout" not in str(statements[0])


def test_unknown_normalization_profile_is_rejected_by_python_and_sql() -> None:
    with pytest.raises(ValueError, match="NormalizationProfile"):
        normalize_text_for_matching("diabetes", profile="invented")
    with pytest.raises(ValueError, match="NormalizationProfile"):
        normalization_expression(
            sa_column("concept_name"),
            profile="invented",
            remove_stop_phrases=True,
        )
