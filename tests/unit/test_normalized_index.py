"""The K expression must preserve every supported normalization variant."""

from __future__ import annotations

import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from oa_configurator import Role
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym
from sqlalchemy.dialects import postgresql

from groundworkers.application.setup import performance_maintenance
from groundworkers.application.setup.databases import (
    GROUNDWORKERS_NORMALIZED_INDEX_TARGETS,
    _normalized_index_diagnostics,
    _squash_index_sql,
)
from groundworkers.base.sql import (
    KEY_DROP_TOKENS,
    NormalizationProfile,
    normalization_expression,
    normalization_key_expression,
    normalization_key_expression_sql,
    normalized_key,
)
from groundworkers.services.vocab import normalize_text_for_matching
from tests.support.stack_config import build_cdm_stack


def _python_key(text: str) -> str:
    tokens = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return " ".join(token for token in tokens if token not in KEY_DROP_TOKENS)


@pytest.mark.parametrize(
    ("profile", "remove_stop_phrases"),
    [
        (NormalizationProfile.VERBATIM, True),
        (NormalizationProfile.VERBATIM, False),
        (NormalizationProfile.DRUG_NAME, True),
        (NormalizationProfile.DRUG_NAME, False),
    ],
)
def test_python_coarse_key_is_lossless_for_all_variants(
    profile: str, remove_stop_phrases: bool
) -> None:
    names = (
        "Type 2 diabetes mellitus NOS",
        "sodium chloride 0.9% w/ sterile water",
        "tablet_W/O coating",
        "drug extended release",
        "drug modified release",
        "  UNKNOWN  ",
        "___",
        "w/o",
        "W/O",
        "wo",
        "tabs\tand\tspaces",
        "é K İ ﬁ",
        "extended release",
        "modified release",
        "",
    )
    for name in names:
        normalized, _ = normalize_text_for_matching(
            name, profile=profile, remove_stop_phrases=remove_stop_phrases
        )
        assert normalized_key(normalized) == _python_key(name)


def test_key_expression_is_inlined_and_version_tolerant() -> None:
    with sa.create_engine("sqlite://").connect() as connection:
        expected = normalization_key_expression_sql(connection, "concept_name")
        query = str(
            normalization_key_expression(sa.column("concept_name")).compile(
                dialect=connection.dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
    # PostgreSQL versions add casts and parentheses differently in pg_indexes.
    indexdef = (
        "CREATE INDEX idx_concept_name_normkey ON public.concept USING btree "
        f"(({expected.replace('concept_name', '((concept_name)::text)')}))"
    )
    assert _squash_index_sql(expected) == _squash_index_sql(query)
    assert _squash_index_sql(expected) in _squash_index_sql(indexdef)


def test_normalized_comparison_remains_variant_specific() -> None:
    col = sa.column("concept_name")
    verbatim = normalization_expression(
        col, profile=NormalizationProfile.VERBATIM, remove_stop_phrases=True
    )
    drug = normalization_expression(
        col, profile=NormalizationProfile.DRUG_NAME, remove_stop_phrases=True
    )
    assert "extended release" not in str(drug)
    assert "extended release" not in str(verbatim) or str(drug) != str(verbatim)


def test_index_names_and_unreadable_listing_diagnostic(monkeypatch) -> None:
    names = {target[3] for target in GROUNDWORKERS_NORMALIZED_INDEX_TARGETS}
    assert names == {
        "idx_concept_name_normkey",
        "idx_concept_synonym_name_normkey",
    }

    monkeypatch.setattr(
        "groundworkers.application.setup.databases.normalized_index_status",
        lambda *_args: None,
    )
    diagnostics = _normalized_index_diagnostics(
        object(), None, {"concept", "concept_synonym"}
    )

    assert diagnostics[0].code == "normalized_indexes_unchecked"


def test_normalized_index_ddl_uses_light_tables_and_preserves_orm_metadata(
    monkeypatch,
) -> None:
    class RecordingConnection:
        dialect = postgresql.dialect()

        def get_execution_options(self):
            return {"schema_translate_map": {Role.VOCAB: "vocab"}}

        def __init__(self) -> None:
            self.statements = []
            self.raw_statements = []

        def execute(self, statement):
            self.statements.append(statement)

        def exec_driver_sql(self, statement):
            self.raw_statements.append(statement)

    connection = RecordingConnection()

    @contextmanager
    def fake_postgres_cdm(_snapshot, *, purpose):
        assert purpose == "Normalized-name indexes"
        yield connection, "cdm"

    monkeypatch.setattr(performance_maintenance, "_postgres_cdm", fake_postgres_cdm)
    monkeypatch.setattr(
        performance_maintenance, "normalized_index_matches", lambda *_args: False
    )
    concept_indexes_before = set(Concept.__table__.indexes)
    synonym_indexes_before = set(Concept_Synonym.__table__.indexes)

    performance_maintenance.populate_normalized_indexes(object())

    creates = [
        statement
        for statement in connection.statements
        if isinstance(statement, sa.schema.CreateIndex)
    ]
    drops = [
        statement
        for statement in connection.statements
        if isinstance(statement, sa.schema.DropIndex)
    ]
    assert [statement.element.name for statement in creates] == [
        "idx_concept_name_normkey",
        "idx_concept_synonym_name_normkey",
    ]
    assert {statement.element.name for statement in drops} == {
        "idx_concept_name_normalized",
        "idx_concept_synonym_name_normalized",
        "idx_concept_name_normkey",
        "idx_concept_synonym_name_normkey",
    }

    compiled_creates = [
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                schema_translate_map={Role.VOCAB: "vocab"},
                render_schema_translate=True,
            )
        )
        for statement in creates
    ]
    compiled_drops = [
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                schema_translate_map={Role.VOCAB: "vocab"},
                render_schema_translate=True,
            )
        )
        for statement in drops
    ]
    assert "ON vocab.concept (" in compiled_creates[0]
    assert "ON vocab.concept_synonym (" in compiled_creates[1]
    assert all("regexp_replace" in statement for statement in compiled_creates)
    assert all("vocab." in statement for statement in compiled_drops)
    assert connection.raw_statements == [
        'ANALYZE vocab.concept',
        'ANALYZE vocab.concept_synonym',
    ]
    assert Concept.__table__.indexes == concept_indexes_before
    assert Concept_Synonym.__table__.indexes == synonym_indexes_before


def test_trigram_indexes_compile_as_gin_expression_indexes(monkeypatch) -> None:
    class RecordingConnection:
        dialect = postgresql.dialect()

        def __init__(self) -> None:
            self.statements = []
            self.raw_statements = []

        def execute(self, statement):
            self.statements.append(statement)

        def exec_driver_sql(self, statement):
            self.raw_statements.append(statement)

    connection = RecordingConnection()

    @contextmanager
    def fake_postgres_cdm(_snapshot, *, purpose):
        assert purpose == "Trigram indexes"
        yield connection, "vocab"

    monkeypatch.setattr(performance_maintenance, "_postgres_cdm", fake_postgres_cdm)
    performance_maintenance.populate_trigram_indexes(object())

    creates = [
        statement
        for statement in connection.statements
        if isinstance(statement, sa.schema.CreateIndex)
    ]
    compiled = [
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                schema_translate_map={Role.VOCAB: "vocab"},
                compile_kwargs={"render_schema_translate": True},
            )
        )
        for statement in creates
    ]
    assert len(compiled) == 2
    assert all("USING gin" in ddl and "gin_trgm_ops" in ddl for ddl in compiled)
    assert all("ON __[SCHEMA_vocab]." in ddl and "lower(" in ddl for ddl in compiled)
    assert connection.raw_statements == ["CREATE EXTENSION IF NOT EXISTS pg_trgm"]


def test_performance_cdm_context_connects_to_vocab_engine(monkeypatch) -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Engine:
        dialect = postgresql.dialect()

        def __init__(self):
            self.connection = Connection()
            self.opened = False
            self.disposed = False

        def begin(self):
            self.opened = True
            return self.connection

        def dispose(self):
            self.disposed = True

    primary, vocab = Engine(), Engine()

    def create_engines(_resolved, *, register_claims):
        assert register_claims is False
        return primary, vocab

    monkeypatch.setattr(performance_maintenance, "create_cdm_engines", create_engines)
    snapshot = SimpleNamespace(stack=build_cdm_stack())

    with performance_maintenance._postgres_cdm(
        snapshot, purpose="Normalized-name indexes"
    ) as (connection, _schema):
        assert connection is vocab.connection

    assert primary.opened is False
    assert vocab.opened is True
    assert primary.disposed is True
    assert vocab.disposed is True
