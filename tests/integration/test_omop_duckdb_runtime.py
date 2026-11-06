"""Real file-backed DuckDB serving contracts; no external model provider is used."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
import sqlalchemy as sa
from oa_configurator import (
    CDMDatabaseConfig,
    ConfigurationError,
    ConnectionConfig,
    GenericDatabaseConfig,
    ModelConfig,
    ProviderConfig,
    Resolver,
    StackConfig,
    VectorStoreConfig,
    save_stack_config,
)
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Class,
    Concept_Relationship,
    Concept_Synonym,
    Domain,
    Relationship,
    Vocabulary,
)
from omop_emb.backends import open_vector_store_writer
from omop_emb.backends.embedding_table import ConceptEmbeddingRecord
from omop_emb.backends.index_config import FlatIndexConfig, HNSWIndexConfig
from omop_emb.config import MetricType
from omop_graph.cli import relationship_classification

from groundworkers.app import build_application
from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.databases import (
    resolve_database_targets,
    verify_database_target,
)
from groundworkers.bootstrap import (
    DatabaseUnavailableError,
    build_app_config_from_stack,
)

MODEL_NAME = "groundworkers-duckdb:v1"


def _seed_vocabulary(resolved):
    engine = resolved.create_engines()[0]
    metadata = sa.MetaData()
    models = (
        Domain,
        Vocabulary,
        Concept_Class,
        Concept,
        Relationship,
        Concept_Synonym,
        Concept_Ancestor,
        Concept_Relationship,
    )
    # Clone DDL metadata: ALTER-capable dialects must not mutate the shared
    # cyclic ORM constraints used by the SQLite tests in the same process.
    tables = {
        model.__tablename__: model.__table__.to_metadata(metadata) for model in models
    }
    tables["concept"].append_column(sa.Column("concept_name_tsvector", sa.String()))
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(
                tables["domain"].insert(),
                [
                    {
                        "domain_id": "Condition",
                        "domain_name": "Condition",
                        "domain_concept_id": 1,
                    }
                ],
            )
            connection.execute(
                tables["vocabulary"].insert(),
                [
                    {
                        "vocabulary_id": "SNOMED",
                        "vocabulary_name": "SNOMED",
                        "vocabulary_concept_id": 1,
                    }
                ],
            )
            connection.execute(
                tables["concept_class"].insert(),
                [
                    {
                        "concept_class_id": "Clinical Finding",
                        "concept_class_name": "Clinical Finding",
                        "concept_class_concept_id": 1,
                    }
                ],
            )
            connection.execute(
                tables["concept"].insert(),
                [
                    {
                        "concept_id": cid,
                        "concept_name": label,
                        "domain_id": "Condition",
                        "vocabulary_id": "SNOMED",
                        "concept_class_id": "Clinical Finding",
                        "standard_concept": "S",
                        "concept_code": str(cid),
                        "valid_start_date": date(2020, 1, 1),
                        "valid_end_date": date(2099, 12, 31),
                    }
                    for cid, label in (
                        (1, "Metadata"),
                        (11, "Diabetes mellitus"),
                        (12, "Metabolic disorder"),
                        (13, "Other condition"),
                    )
                ],
            )
            connection.execute(
                tables["relationship"].insert(),
                [
                    {
                        "relationship_id": rid,
                        "relationship_name": rid,
                        "is_hierarchical": "1",
                        "defines_ancestry": "1",
                        "reverse_relationship_id": reverse,
                        "relationship_concept_id": 1,
                    }
                    for rid, reverse in (
                        ("Is a", "Subsumes"),
                        ("Subsumes", "Is a"),
                        ("Box of", "Has box"),
                        ("Has box", "Box of"),
                    )
                ],
            )
            connection.execute(
                tables["concept_synonym"].insert(),
                [
                    {
                        "concept_id": 11,
                        "concept_synonym_name": "Sugar disease",
                        "language_concept_id": 1,
                    }
                ],
            )
            connection.execute(
                tables["concept_ancestor"].insert(),
                [
                    {
                        "ancestor_concept_id": ancestor,
                        "descendant_concept_id": descendant,
                        "min_levels_of_separation": depth,
                        "max_levels_of_separation": depth,
                    }
                    for ancestor, descendant, depth in (
                        (11, 11, 0),
                        (12, 12, 0),
                        (12, 11, 1),
                    )
                ],
            )
            connection.execute(
                tables["concept_relationship"].insert(),
                [
                    {
                        "concept_id_1": source,
                        "concept_id_2": target,
                        "relationship_id": rid,
                        "valid_start_date": date(2020, 1, 1),
                        "valid_end_date": date(2099, 12, 31),
                    }
                    for source, target, rid in (
                        (11, 12, "Is a"),
                        (12, 11, "Subsumes"),
                        (11, 13, "Box of"),
                    )
                ],
            )
        relationship_classification(resolved=resolved)
        relationship_classification(resolved=resolved)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def duckdb_stack(tmp_path_factory):
    root = tmp_path_factory.mktemp("duckdb-runtime")
    stack = StackConfig(
        connections={
            "cdm": ConnectionConfig(
                dialect="duckdb", database_name=str(root / "cdm.duckdb")
            ),
            "emb": ConnectionConfig(
                dialect="duckdb", database_name=str(root / "emb.duckdb")
            ),
        },
        databases={
            "cdm": CDMDatabaseConfig(connection="cdm"),
            "emb": GenericDatabaseConfig(connection="emb"),
        },
        providers={
            "offline": ProviderConfig(provider="ollama", base_url="http://127.0.0.1:1")
        },
        models={
            "fixture": ModelConfig(
                provider="offline",
                model=MODEL_NAME,
                embeddings=True,
                embedding_dim=3,
                query_prefix="query: ",
            )
        },
        vector_stores={
            "vectors": VectorStoreConfig(backend_type="duckdbvss", database="emb")
        },
        tools={
            "groundworkers": {
                "cdm_db": "cdm",
                "embedding_model_name": "fixture",
                "vector_store_name": "vectors",
            }
        },
    )
    resolver = Resolver(stack)
    _seed_vocabulary(resolver.resolve_database("cdm"))
    writer = open_vector_store_writer(resolver.resolve_vector_store("vectors"))
    index = HNSWIndexConfig(metric_type=MetricType.COSINE)
    try:
        writer.register_model(
            model_name=MODEL_NAME,
            provider_type="ollama",
            index_config=FlatIndexConfig(metric_type=MetricType.COSINE),
            dimensions=3,
        )
        writer.upsert_embeddings(
            model_name=MODEL_NAME,
            records=tuple(
                ConceptEmbeddingRecord(cid, "Condition", "SNOMED", True, True)
                for cid in (11, 12, 13)
            ),
            embeddings=np.asarray(
                [[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0]], dtype=np.float32
            ),
        )
        writer.rebuild_index(model_name=MODEL_NAME, index_config=index)
        assert writer.physical_indexes(MODEL_NAME)
    finally:
        writer.close()
    return stack


@pytest.fixture
def duckdb_app(duckdb_stack):
    app = build_application(build_app_config_from_stack(duckdb_stack))
    try:
        yield app
    finally:
        if app.adapters.omop_emb is not None:
            app.adapters.omop_emb.close()
        app.config.cdm_engine.dispose()


def test_duckdb_serves_lexical_graph_and_named_relationship_queries(duckdb_app):
    services = duckdb_app.services
    assert (
        services.vocab.search_exact("Diabetes mellitus")[0].concept["concept_id"] == 11
    )
    assert (
        services.vocab.search_normalized_with_plan(
            "DIABETES MELLITUS", vocabulary_id="SNOMED"
        )[0][0].concept["concept_id"]
        == 11
    )
    assert services.vocab.fts_available is False
    assert services.vocab.search_fulltext("diabetes") == ([], False)
    assert "DuckDB" in services.vocab.probe_fulltext()[1]
    assert 12 in {row["concept_id"] for row in services.graph.get_ancestors(11, 2)}
    neighborhood = services.mapping.concept_relationship_neighborhood(
        [11], relationship_ids=["Box of"], max_depth=1
    )
    assert neighborhood["returned_edge_count"] == 1
    assert neighborhood["edges"][0]["object_id"] == 13


def test_duckdb_embedding_reader_serves_stored_and_encoded_queries(
    duckdb_app, monkeypatch
):
    class FixedEncoder:
        model = MODEL_NAME
        provider = "ollama"

        def embed_texts(self, texts, *, role, batch_size=None):
            return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(
        "omop_llm.build_model_backend_from_resolved", lambda _: FixedEncoder()
    )
    adapter = duckdb_app.adapters.omop_emb
    assert adapter.index_status()["available"] is True
    assert (
        adapter.get_neighbours(11, limit=2, model_name=None)["results"][0]["concept_id"]
        == 12
    )
    result = adapter.search(
        "a test query",
        limit=2,
        domain="Condition",
        vocabulary="SNOMED",
        standard_only=True,
        active_only=True,
        model_name=None,
    )
    assert result["results"][0]["concept_id"] == 11
    assert duckdb_app.config.embedding_model.query_prefix == "query: "
    with pytest.raises(sa.exc.DBAPIError, match=r"[Rr]ead.only"):
        with duckdb_app.config.cdm_engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE must_not_exist (value INT)"))
    with pytest.raises(sa.exc.DBAPIError, match=r"[Rr]ead.only"):
        with adapter._get_backend().emb_engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE must_not_exist (value INT)"))


def test_duckdb_readiness_coexists_with_runtime_and_second_process(
    duckdb_app, tmp_path
):
    path = tmp_path / "runtime.toml"
    save_stack_config(duckdb_app.config.stack, path)
    snapshot = load_configuration(config_path=path)
    results = [
        verify_database_target(target) for target in resolve_database_targets(snapshot)
    ]
    assert all(result.connected for result in results), results
    assert not any(result.failure for result in results)
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import sqlalchemy as sa; from groundworkers.bootstrap import build_app_config; "
            "c=build_app_config(config_path=sys.argv[1]); "
            "conn=c.cdm_engine.connect(); print(conn.scalar(sa.text('SELECT count(*) FROM concept'))); "
            "conn.close(); c.cdm_engine.dispose()",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert child.stdout.strip() == "4"


def test_duckdb_missing_cdm_does_not_create_a_file(duckdb_stack, tmp_path):
    stack = duckdb_stack.model_copy(deep=True)
    path = tmp_path / "missing.duckdb"
    stack.connections["cdm"].database_name = str(path)
    with pytest.raises(DatabaseUnavailableError):
        build_app_config_from_stack(stack)
    assert not path.exists()


@pytest.mark.parametrize("alias", ["absolute", "relative", "tilde", "symlink"])
def test_duckdb_rejects_colocated_embedding_file(
    duckdb_stack, tmp_path, monkeypatch, alias
):
    stack = duckdb_stack.model_copy(deep=True)
    path = Path(stack.connections["cdm"].database_name)
    monkeypatch.chdir(path.parent)
    monkeypatch.setenv("HOME", str(path.parent))
    if alias == "symlink":
        linked = tmp_path / "alias.duckdb"
        linked.symlink_to(path)
        database_name = str(linked)
    else:
        database_name = {
            "absolute": str(path),
            "relative": "./" + path.name,
            "tilde": "~/" + path.name,
        }[alias]
    stack.connections["emb"].database_name = database_name
    message = (
        "missing or is not a regular file"
        if alias == "tilde"
        else "separate prebuilt file"
    )
    with pytest.raises(ConfigurationError, match=message):
        build_app_config_from_stack(stack)


@pytest.mark.parametrize("topology", ["memory", "separate_vocab", "mixed_dialect"])
def test_duckdb_rejects_unsupported_cdm_topologies(duckdb_stack, tmp_path, topology):
    stack = duckdb_stack.model_copy(deep=True)
    if topology == "memory":
        stack.connections["cdm"].database_name = ":memory:"
        message = "existing CDM file"
    else:
        stack.connections["other"] = ConnectionConfig(
            dialect="sqlite" if topology == "mixed_dialect" else "duckdb",
            database_name=str(tmp_path / "other.db"),
        )
        stack.databases["cdm"].vocab_connection = "other"
        message = "primary and vocabulary roles in one file"
    with pytest.raises(DatabaseUnavailableError, match=message):
        build_app_config_from_stack(stack)


def test_duckdb_setup_refuses_writers_and_uses_separate_store(duckdb_stack, tmp_path):
    from groundworkers.application.setup.configuration_provider import (
        _vector_store_fields,
    )
    from groundworkers.application.setup.embedding_population import (
        build_embedding_population_command,
    )
    from groundworkers.application.setup.embedding_setup import (
        initialize_embedding_store,
        load_embedding_configuration,
    )
    from groundworkers.application.setup.models import EmbeddingPopulationRequest
    from groundworkers.tui.state import SetupSession
    from groundworkers.tui.wizards.graph_maintenance import (
        GraphMaintenanceWizardController,
    )

    fields = {field.key: field for field in _vector_store_fields(duckdb_stack)}
    assert fields["backend_type"].default == "duckdbvss"
    assert fields["store_location"].default == "separate"
    assert {option.value for option in fields["store_location"].choices} == {"separate"}
    path = tmp_path / "runtime.toml"
    save_stack_config(duckdb_stack, path)
    snapshot = load_configuration(config_path=path)
    initializer = Mock(side_effect=AssertionError("must never open a writer"))
    result = initialize_embedding_store(snapshot, initializer=initializer)
    assert result.failure is not None
    initializer.assert_not_called()
    configuration = load_embedding_configuration(snapshot)
    assert configuration is not None
    request = EmbeddingPopulationRequest(
        standard_only=True,
        vocabulary_mode="all",
        vocabularies=(),
        limit=None,
        batch_size=10,
    )
    with pytest.raises(ValueError, match="read-only"):
        build_embedding_population_command(configuration, request)
    controller = GraphMaintenanceWizardController(SetupSession(config_path=path), None)
    controller.submit({"load_relationship_classification": True})
    assert "read-only" in controller.apply().detail


@pytest.mark.parametrize(
    "target", ["missing", "relative", "directory", "memory", "tilde"]
)
def test_duckdb_missing_embedding_file_is_reported_without_creation(
    duckdb_stack, tmp_path, monkeypatch, target
):
    from groundworkers.application.setup.embedding_population import (
        load_embedding_coverage_report,
    )

    stack = duckdb_stack.model_copy(deep=True)
    path = tmp_path / "missing-emb.duckdb"
    if target == "directory":
        path.mkdir()
    monkeypatch.chdir(tmp_path)
    stack.connections["emb"].database_name = (
        ":memory:"
        if target == "memory"
        else path.name
        if target == "relative"
        else str(path)
    )
    if target == "tilde":
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "existing-emb.duckdb").touch()
        stack.connections["emb"].database_name = "~/existing-emb.duckdb"
        path = tmp_path / "~" / "existing-emb.duckdb"
    construct_engines = Mock(
        side_effect=AssertionError("validation must precede engines")
    )
    monkeypatch.setattr(
        "groundworkers.bootstrap.create_runtime_cdm_engines", construct_engines
    )
    with pytest.raises(ConfigurationError) as raised:
        build_app_config_from_stack(stack)
    assert (":memory:" if target == "memory" else str(path)) in str(raised.value)
    construct_engines.assert_not_called()
    if target != "directory":
        assert not path.exists()

    config_path = tmp_path / "runtime.toml"
    save_stack_config(stack, config_path)
    report = load_embedding_coverage_report(load_configuration(config_path=config_path))
    assert report is not None
    assert report.coverage.available is False
    assert str(raised.value) in report.coverage.blocker


@pytest.mark.parametrize("profile", ["verbatim", "aggressive", "drug_name"])
@pytest.mark.parametrize("remove_stop_phrases", [True, False])
def test_duckdb_native_normalization_matches_python(
    duckdb_app, profile, remove_stop_phrases
):
    from groundworkers.base.sql import normalization_expression
    from groundworkers.services.vocab import normalize_text_for_matching

    labels = (
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
        "",
    )
    with duckdb_app.config.cdm_engine.connect() as connection:
        for label in labels:
            expected, _ = normalize_text_for_matching(
                label, profile=profile, remove_stop_phrases=remove_stop_phrases
            )
            actual = connection.scalar(
                sa.select(
                    normalization_expression(
                        sa.literal(label),
                        profile=profile,
                        remove_stop_phrases=remove_stop_phrases,
                        dialect_name="duckdb",
                    )
                )
            )
            assert actual == expected, (label, profile, remove_stop_phrases)
