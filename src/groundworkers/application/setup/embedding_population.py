from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from oa_configurator import Resolver
from omop_emb.config import OmopEmbConfig, ProviderType
from omop_emb.embeddings.embedding_providers import get_provider_from_provider_type
from sqlalchemy import create_engine, inspect, text
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
from groundworkers.config import (
    resolve_cdm_resource_name,
    resolve_embedding_resource_name,
)

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
        omop_emb = OmopEmbConfig.from_stack(snapshot.stack)
        cdm_engine, cdm_schema = _cdm_engine_and_schema(snapshot)
        eligible = _eligible_counts_by_vocabulary(
            cdm_engine,
            schema=cdm_schema,
            standard_only=standard_only,
        )
        canonical_model = _canonical_model_name(
            configuration.model_name,
            provider_kind=configuration.provider_kind,
        )
        index = _embedding_index_snapshot(
            snapshot,
            configuration=configuration,
            omop_emb=omop_emb,
            canonical_model=canonical_model,
        )
        embedded = (
            _embedded_counts_by_vocabulary(
                snapshot,
                omop_emb=omop_emb,
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
    except Exception as exc:  # noqa: BLE001 - rendered as setup state
        coverage = CoverageSnapshot(
            scope=scope,
            available=False,
            blocker=f"Embedding coverage could not be loaded: {exc}",
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
    profile: str | None = None,
) -> EmbeddingPopulationCommand:
    """Build the omop-emb command used to populate missing concept embeddings."""

    executable = shutil.which("omop-emb") or "omop-emb"
    argv: list[str] = [
        executable,
        "embeddings",
        "add-embeddings",
        "--model",
        configuration.model_name,
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
    if profile is not None:
        environment.append(("OA_ACTIVE_PROFILE", profile))
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
        process = subprocess.Popen(  # noqa: S603 - argv is generated, not shell text
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
    resource = resolver.resolve_resource(resolve_cdm_resource_name(snapshot.stack))
    return create_engine(resource.database.url), resource.cdm_schema


def _embedding_engine(
    snapshot: ConfigurationSnapshot,
    *,
    omop_emb: OmopEmbConfig,
) -> Engine:
    assert snapshot.stack is not None
    if omop_emb.backend == "pgvector":
        resolver = Resolver(snapshot.stack)
        resource = resolver.resolve_resource(
            resolve_embedding_resource_name(snapshot.stack)
        )
        return create_engine(resource.database.url)
    if omop_emb.backend == "sqlitevec":
        from omop_emb.backends.sqlitevec.sqlitevec_backend import (
            create_sqlitevec_engine,
        )

        if omop_emb.sqlite_path is None:
            raise ValueError("sqlitevec backend is missing sqlite_path.")
        return create_sqlitevec_engine(_resolved_path(snapshot, omop_emb.sqlite_path))
    raise ValueError(f"Unsupported embedding backend: {omop_emb.backend}")


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
    snapshot: ConfigurationSnapshot,
    *,
    omop_emb: OmopEmbConfig,
    storage_identifier: str | None,
    standard_only: bool,
) -> Mapping[str, int]:
    if storage_identifier is None:
        return {}
    engine = _embedding_engine(snapshot, omop_emb=omop_emb)
    table = _quote_identifier(engine, storage_identifier)
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
    with engine.connect() as connection:
        return {str(row.vocabulary_id): int(row.n) for row in connection.execute(query)}


def _embedding_index_snapshot(
    snapshot: ConfigurationSnapshot,
    *,
    configuration: EmbeddingConfiguration,
    omop_emb: OmopEmbConfig,
    canonical_model: str,
) -> EmbeddingIndexSnapshot:
    engine = _embedding_engine(snapshot, omop_emb=omop_emb)
    record = _registered_model(
        engine, backend=omop_emb.backend, model_name=canonical_model
    )
    if record is None:
        return EmbeddingIndexSnapshot(model_name=canonical_model, registered=False)
    physical_indexes = _physical_vector_indexes(
        engine,
        table_name=record.storage_identifier,
    )
    return EmbeddingIndexSnapshot(
        model_name=canonical_model,
        registered=True,
        storage_identifier=record.storage_identifier,
        registry_index_type=_enum_value(record.index_type),
        registry_metric=_enum_value(record.metric_type),
        physical_indexes=physical_indexes,
        drop_sql=tuple(_drop_index_sql(engine, name) for name in physical_indexes),
    )


def _registered_model(engine: Engine, *, backend: str, model_name: str) -> Any | None:
    if backend == "pgvector":
        from omop_emb.backends.pgvector.pg_backend import PGVectorEmbeddingBackend

        return PGVectorEmbeddingBackend(engine).get_registered_model(
            model_name=model_name
        )
    if backend == "sqlitevec":
        from omop_emb.backends.sqlitevec.sqlitevec_backend import (
            SQLiteVecEmbeddingBackend,
        )

        return SQLiteVecEmbeddingBackend(engine).get_registered_model(
            model_name=model_name
        )
    return None


def _physical_vector_indexes(
    engine: Engine,
    *,
    table_name: str,
) -> tuple[str, ...]:
    if engine.dialect.name != "postgresql":
        return ()
    expected_prefix = f"idx_{table_name}_"
    return tuple(
        str(item["name"])
        for item in inspect(engine).get_indexes(table_name)
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
    provider = get_provider_from_provider_type(ProviderType(provider_kind))
    return provider.canonical_model_name(model_name)


def _resolved_path(snapshot: ConfigurationSnapshot, value: str) -> str:
    path = Path(value).expanduser()
    if path.is_absolute() or snapshot.path is None:
        return str(path)
    return str(snapshot.path.parent / path)


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
