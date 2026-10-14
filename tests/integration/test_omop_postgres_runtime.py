from __future__ import annotations

import os

import pytest
from oa_configurator import Role
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine

from groundworkers.app import build_adapters, build_application
from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.performance_maintenance import (
    populate_normalized_indexes,
    populate_trigram_indexes,
)
from groundworkers.base.errors import GroundworkersError
from groundworkers.bootstrap import build_app_config, build_app_config_from_stack


@pytest.fixture(scope="module")
def indexed_runtime_config():
    config_path = os.getenv("GROUNDWORKERS_CONFIG_PATH") or os.getenv(
        "GROUNDWORKERS_CONFIG"
    )
    if os.getenv("GROUNDWORKERS_REQUIRE_INTEGRATION") != "1":
        pytest.skip("PostgreSQL schema and runtime checks run in the required database lane")
    if not config_path:
        raise RuntimeError("required PostgreSQL checks need GROUNDWORKERS_CONFIG_PATH")

    snapshot = load_configuration(config_path=config_path)
    populate_trigram_indexes(snapshot)
    populate_normalized_indexes(snapshot)
    config = build_app_config(config_path=config_path)
    schema = config.cdm_database.schema_for_role(Role.VOCAB)
    indexes = {
        index["name"]
        for index in inspect(config.cdm_vocab_engine).get_indexes(
            "concept", schema=schema
        )
    }
    assert "idx_concept_name_normkey" in indexes
    assert "idx_concept_lower_name_trgm" in indexes
    yield config, schema
    config.cdm_engine.dispose()
    if config.cdm_vocab_engine is not config.cdm_engine:
        config.cdm_vocab_engine.dispose()


@pytest.mark.required_integration
def test_postgres_created_indexes_reflect_and_serve_normalized_search(
    indexed_runtime_config,
):
    config, schema = indexed_runtime_config
    app = build_application(config)

    matches, plan = app.services.vocab.search_normalized_with_plan(
        "Type 2 diabetes mellitus", domain="Condition"
    )

    assert plan == "indexed"
    assert 201826 in {match.concept_id for match in matches}
    assert config.cdm_database.schema_for_role(Role.VOCAB) == schema


@pytest.mark.required_integration
def test_postgres_restricted_runtime_role_reads_split_schema_without_write_grants(
    indexed_runtime_config,
):
    config, vocab_schema = indexed_runtime_config
    from tests.support.embedding_contract_fixture import (
        MODEL_NAME,
        seed_embedding_contract,
    )

    seed_embedding_contract(config)
    admin_engine = create_engine(os.environ["GROUNDWORKERS_TEST_DATABASE_URL"])
    runtime = None
    adapters = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gw_runtime') THEN "
                    "CREATE ROLE gw_runtime LOGIN PASSWORD 'gw_runtime_fixture'; "
                    "ELSE ALTER ROLE gw_runtime LOGIN PASSWORD 'gw_runtime_fixture'; END IF; "
                    "END $$"
                )
            )
            connection.execute(text("GRANT CONNECT ON DATABASE test_db TO gw_runtime"))
            schemas = connection.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema'"
                )
            ).scalars().all()
            quote = connection.dialect.identifier_preparer.quote
            for schema in schemas:
                quoted_schema = quote(schema)
                connection.execute(text(f"GRANT USAGE ON SCHEMA {quoted_schema} TO gw_runtime"))
                connection.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {quoted_schema} TO gw_runtime"))

        stack = config.stack.model_copy(deep=True)
        connection = stack.connections["pg_test"]
        stack.connections["pg_test"] = connection.model_copy(
            update={"user": "gw_runtime", "password": "gw_runtime_fixture"}
        )
        runtime = build_app_config_from_stack(stack)
        assert runtime.cdm_database.schema_for_role(Role.PRIMARY) == "gw_primary"
        assert runtime.cdm_database.schema_for_role(Role.VOCAB) == vocab_schema
        adapters = build_adapters(runtime)
        graph = adapters.omop_graph
        assert graph is not None and graph.is_available()
        assert adapters.omop_emb is not None

        statements = []

        def record_statement(_conn, _cursor, statement, *_args):
            statements.append(statement)

        event.listen(Engine, "before_cursor_execute", record_statement)
        try:
            concept = graph.get_concept(201826)
            assert concept is not None and concept["concept_name"] == "Type 2 diabetes mellitus"
            neighbours = adapters.omop_emb.get_neighbours(
                201826, limit=5, model_name=MODEL_NAME
            )
            assert neighbours["results"]
        finally:
            event.remove(Engine, "before_cursor_execute", record_statement)

        assert statements
        assert not any(
            statement.lstrip().split(None, 1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE"}
            for statement in statements
        )
        with runtime.cdm_vocab_engine.connect() as connection:
            can_insert = connection.execute(
                text("SELECT has_table_privilege(current_user, :table_name, 'INSERT')"),
                {"table_name": f"{vocab_schema}.concept"},
            ).scalar_one()
        assert can_insert is False
    finally:
        if adapters is not None:
            if adapters.omop_graph is not None:
                adapters.omop_graph.close()
            if adapters.omop_emb is not None:
                adapters.omop_emb.close()
        if runtime is not None:
            runtime.cdm_engine.dispose()
            if runtime.cdm_vocab_engine is not runtime.cdm_engine:
                runtime.cdm_vocab_engine.dispose()
        try:
            with admin_engine.begin() as connection:
                role_exists = connection.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = 'gw_runtime'")
                ).scalar_one_or_none()
                if role_exists:
                    connection.execute(text("DROP OWNED BY gw_runtime"))
                    connection.execute(text("DROP ROLE gw_runtime"))
        finally:
            admin_engine.dispose()


@pytest.mark.required_integration
def test_postgres_statement_timeout_rollback_keeps_session_usable(
    indexed_runtime_config,
    monkeypatch,
):
    config, _schema = indexed_runtime_config
    from groundworkers.services import vocab

    monkeypatch.setattr(vocab, "NORMALIZED_SEARCH_TIMEOUT_MS", 1)
    app = build_application(config)
    assert (
        app.services.vocab._normalized_search_plan(
            domain="Condition",
            vocabulary_id=None,
            parent_ids=None,
            include_synonyms=False,
            allow_unindexed_scan=False,
        )
        == "indexed"
    )
    injected = False

    def delay_app_search(_conn, cursor, statement, *_args):
        nonlocal injected
        if not injected and "set_config" in statement.lower():
            injected = True
            cursor.execute("SELECT pg_sleep(0.05)")

    event.listen(config.cdm_vocab_engine, "after_cursor_execute", delay_app_search)
    try:
        with pytest.raises(GroundworkersError) as excinfo:
            app.services.vocab.search_normalized_with_plan(
                "Type 2 diabetes mellitus", domain="Condition"
            )
        assert excinfo.value.code == "QUERY_TIMEOUT"
    finally:
        event.remove(config.cdm_vocab_engine, "after_cursor_execute", delay_app_search)

    results, plan = app.services.vocab.search_normalized_with_plan(
        "Type 2 diabetes mellitus", domain="Condition"
    )
    assert injected
    assert plan == "indexed"
    assert 201826 in {match.concept_id for match in results}
