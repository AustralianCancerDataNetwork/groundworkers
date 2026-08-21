from __future__ import annotations

import re
from datetime import date

from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
    Concept_Synonym,
)
from sqlalchemy import create_engine, event

from groundworkers.adapters.cdm import CDMAdapter
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.vocab import VocabService
from groundworkers.tools.search_tools import register_search_tools


def _service(tmp_path) -> VocabService:
    engine = create_engine(f"sqlite:///{tmp_path / 'vocabulary.db'}")

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
            ({"concept_id": 1, "concept_synonym_name": "Diabetes", "language_concept_id": 0},),
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


def test_vocab_service_exact_normalized_sidecar_and_standard_navigation(tmp_path) -> None:
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
    assert navigation[0].standard_concepts[0].relationship_id == "self"
    assert navigation[1].standard_concepts[0].concept_id == 1


def test_fulltext_probe_uses_the_first_available_concept_label(tmp_path, monkeypatch) -> None:
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
