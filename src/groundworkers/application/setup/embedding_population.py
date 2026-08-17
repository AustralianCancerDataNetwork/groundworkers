from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from oa_configurator import ResolvedVectorStore, Resolver
from omop_emb import EmbeddingBackend
from omop_emb.backends import resolve_backend_from_resolved_vector_store
from omop_llm import canonical_model_name
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from groundworkers.application.setup.embedding_coverage import calculate_coverage
from groundworkers.application.setup.embedding_setup import load_embedding_configuration
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    CoverageScope,
    CoverageSnapshot,
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
    EmbeddingIndexSnapshot,
    EmbeddingPopulationCommand,
    EmbeddingPopulationLaunch,
    EmbeddingPopulationRequest,
)
from groundworkers.config import GroundworkersConfig

DEFAULT_EMBEDDING_BATCH_SIZE = 100
DEFAULT_EMBEDDING_BACKFILL_LIMIT = 1000


def load_embedding_coverage_report(
    snapshot: ConfigurationSnapshot,
    *,
    standard_only: bool = True,
) -> EmbeddingCoverageReport | None:
    """Inspect CDM/vector coverage for the configured embedding model."""

    configuration = load_embedding_configuration(snapshot)
    if configuration is None or snapshot.stack is None:
        return None
    scope = CoverageScope(
        model_name=configuration.model_name,
        metric="cosine",
        vocabularies=(),
        standard_only=standard_only,
        valid_only=False,
    )
    try:
        groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
        if (
            groundworkers.embedding_model_name is None
            or groundworkers.vector_store_name is None
        ):
            raise ValueError("Embedding model and vector store references are required.")
        resolver = Resolver(snapshot.stack)
        resolved_model = resolver.resolve_model(groundworkers.embedding_model_name)
        vector_store = resolver.resolve_vector_store(
            groundworkers.vector_store_name
        )
        backend = resolve_backend_from_resolved_vector_store(vector_store)
        cdm_engine, cdm_schema = _cdm_engine_and_schema(snapshot)
        try:
            eligible = _eligible_counts_by_vocabulary(
                cdm_engine,
                schema=cdm_schema,
                standard_only=standard_only,
            )
        finally:
            cdm_engine.dispose()
        canonical_model = _canonical_model_name(
            resolved_model.model,
            provider_kind=resolved_model.provider.provider,
        )
        index = _embedding_index_snapshot(
            backend=backend,
            vector_store=vector_store,
            canonical_model=canonical_model,
        )
        embedded = (
            _embedded_counts_by_vocabulary(
                vector_store=vector_store,
                storage_identifier=index.storage_identifier,
                standard_only=standard_only,
            )
            if index.registered and index.storage_identifier is not None
            else {}
        )
        coverage_scope = CoverageScope(
            model_name=canonical_model,
            metric=index.registry_metric or "cosine",
            vocabularies=tuple(sorted(eligible)),
            standard_only=standard_only,
            valid_only=False,
        )
        coverage = calculate_coverage(
            coverage_scope,
            eligible=eligible,
            embedded={
                key: min(value, eligible.get(key, value))
                for key, value in embedded.items()
            },
        )
    except Exception as exc:  # noqa: BLE001 - rendered as secret-safe setup state
        coverage = CoverageSnapshot(
            scope=scope,
            available=False,
            blocker=(
                "Embedding coverage could not be loaded because setup failed with "
                f"{type(exc).__name__}."
            ),
        )
        index = EmbeddingIndexSnapshot(
            model_name=configuration.model_name,
            registered=False,
        )
    return EmbeddingCoverageReport(
        configuration=configuration,
        coverage=coverage,
        index=index,
    )


def build_embedding_population_command(
    configuration: EmbeddingConfiguration,
    request: EmbeddingPopulationRequest,
    *,
    config_path: str | Path | None = None,
) -> EmbeddingPopulationCommand:
    """Build the omop-emb command used to populate missing concept embeddings."""

    executable = shutil.which("omop-emb") or "omop-emb"
    argv: list[str] = [
        executable,
        "embeddings",
        "add-embeddings",
        "--model-name",
        configuration.model_entry_name,
        "--batch-size",
        str(request.batch_size),
    ]
    if request.standard_only:
        argv.append("--standard-only")
    for vocabulary in request.vocabularies:
        argv.extend(("--vocabulary", vocabulary))
    if request.limit is not None:
        argv.extend(("--num-embeddings", str(request.limit)))
    environment: list[tuple[str, str]] = []
    if config_path is not None:
        environment.append(("OA_CONFIG_PATH", str(Path(config_path).expanduser())))
    return EmbeddingPopulationCommand(
        argv=tuple(argv),
        environment=tuple(environment),
    )


def launch_embedding_population(
    command: EmbeddingPopulationCommand,
    *,
    log_dir: str | Path = "/tmp",
) -> EmbeddingPopulationLaunch:
    """Start an omop-emb population command and return immediately."""

    log_path = Path(log_dir) / (
        f"groundworkers-omop-emb-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.log"
    )
    env = os.environ.copy()
    env.update(dict(command.environment))
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command.argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    return EmbeddingPopulationLaunch(
        command=command,
        pid=process.pid,
        log_path=log_path,
    )


def _cdm_engine_and_schema(snapshot: ConfigurationSnapshot) -> tuple[Engine, str]:
    assert snapshot.stack is not None
    resolver = Resolver(snapshot.stack)
    groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
    database = resolver.resolve_database(groundworkers.cdm_db)
    return database.create_engine(), database.schema_name or "main"


def _embedding_engine(
    vector_store: ResolvedVectorStore,
) -> Engine:
    return vector_store.database.create_engine()


def _eligible_counts_by_vocabulary(
    engine: Engine,
    *,
    schema: str,
    standard_only: bool,
) -> Mapping[str, int]:
    concept_table = _qualified_name(engine, schema, "concept")
    where = "WHERE standard_concept = 'S'" if standard_only else ""
    query = text(
        "SELECT vocabulary_id, COUNT(*) AS n "
        f"FROM {concept_table} "
        f"{where} "
        "GROUP BY vocabulary_id "
        "ORDER BY vocabulary_id"
    )
    with engine.connect() as connection:
        return {str(row.vocabulary_id): int(row.n) for row in connection.execute(query)}


def _embedded_counts_by_vocabulary(
    *,
    vector_store: ResolvedVectorStore,
    storage_identifier: str | None,
    standard_only: bool,
) -> Mapping[str, int]:
    if storage_identifier is None:
        return {}
    engine = _embedding_engine(vector_store)
    table = _qualified_name(
        engine,
        vector_store.database.schema_name,
        storage_identifier,
    )
    where = "WHERE is_standard = true" if standard_only else ""
    if engine.dialect.name == "sqlite" and standard_only:
        where = "WHERE is_standard = 1"
    query = text(
        "SELECT vocabulary_id, COUNT(*) AS n "
        f"FROM {table} "
        f"{where} "
        "GROUP BY vocabulary_id "
        "ORDER BY vocabulary_id"
    )
    try:
        with engine.connect() as connection:
            return {
                str(row.vocabulary_id): int(row.n)
                for row in connection.execute(query)
            }
    finally:
        engine.dispose()


def _embedding_index_snapshot(
    *,
    backend: EmbeddingBackend,
    vector_store: ResolvedVectorStore,
    canonical_model: str,
) -> EmbeddingIndexSnapshot:
    record = backend.get_registered_model(model_name=canonical_model)
    if record is None:
        return EmbeddingIndexSnapshot(model_name=canonical_model, registered=False)
    engine = _embedding_engine(vector_store)
    try:
        physical_indexes = _physical_vector_indexes(
            engine,
            table_name=record.storage_identifier,
            schema=vector_store.database.schema_name,
        )
        return EmbeddingIndexSnapshot(
            model_name=canonical_model,
            registered=True,
            storage_identifier=record.storage_identifier,
            registry_index_type=_enum_value(record.index_type),
            registry_metric=_enum_value(record.metric_type),
            physical_indexes=physical_indexes,
            drop_sql=tuple(
                _drop_index_sql(engine, name) for name in physical_indexes
            ),
        )
    finally:
        engine.dispose()


def _physical_vector_indexes(
    engine: Engine,
    *,
    table_name: str,
    schema: str | None,
) -> tuple[str, ...]:
    if engine.dialect.name != "postgresql":
        return ()
    expected_prefix = f"idx_{table_name}_"
    return tuple(
        str(item["name"])
        for item in inspect(engine).get_indexes(table_name, schema=schema)
        if str(item["name"]).startswith(expected_prefix)
    )


def _drop_index_sql(engine: Engine, index_name: str) -> str:
    return f"DROP INDEX IF EXISTS {_quote_identifier(engine, index_name)};"


def _qualified_name(engine: Engine, schema: str | None, table: str) -> str:
    if not schema or (engine.dialect.name == "sqlite" and schema == "main"):
        return _quote_identifier(engine, table)
    return f"{_quote_identifier(engine, schema)}.{_quote_identifier(engine, table)}"


def _quote_identifier(engine: Engine, name: str) -> str:
    return engine.dialect.identifier_preparer.quote(name)


def _canonical_model_name(model_name: str, *, provider_kind: str) -> str:
    return canonical_model_name(provider_kind, model_name)


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
