from __future__ import annotations

import re
import shutil
from collections.abc import Mapping
from pathlib import Path

from oa_configurator import ResolvedVectorStore, Resolver, safe_endpoint
from omop_emb import EmbeddingBackend
from omop_emb.backends import resolve_backend_from_resolved_vector_store
from omop_llm import canonical_model_name
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from groundworkers.application.setup.embedding_coverage import calculate_coverage
from groundworkers.application.setup.embedding_setup import load_embedding_configuration
from groundworkers.application.setup.maintenance import launch_maintenance_command
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
from groundworkers.base.results import enum_value
from groundworkers.base.sql import quote_identifier
from groundworkers.config import GroundworkersConfig

_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)

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
    except Exception as exc:
        # Broad except: rendered as secret-safe setup state.
        coverage = CoverageSnapshot(
            scope=scope,
            available=False,
            blocker=_coverage_blocker(exc),
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


def _coverage_blocker(exc: Exception) -> str:
    """Say why coverage failed, without handing a driver's DSN to the screen.

    ``ValueError`` here is a configuration verdict: the required references are
    missing, or a provider has refused the model name it was given. Those
    messages are authored for an operator and are the whole point of the
    failure, so withholding them -- as this used to, naming only the exception
    class -- turned an actionable answer into a dead end.

    Every other exception is an operational failure raised by a driver or an
    engine, and those routinely quote the connection string that failed. They
    keep the class-name-only form. URLs are scrubbed from whatever is shown, so
    a message that does carry one cannot leak a credential either way.
    """
    if isinstance(exc, ValueError):
        detail = _scrub_urls(str(exc).strip())
        if detail:
            return f"Embedding coverage could not be loaded. {detail}"
    return (
        "Embedding coverage could not be loaded because setup failed with "
        f"{type(exc).__name__}."
    )


def _scrub_urls(text: str) -> str:
    """Mask credentials in any URL the text carries.

    Delegates the definition of a safe URL to ``oa_configurator.safe_endpoint``,
    the same primitive its logging filter uses. Only the regex is local, because
    oa-configurator keeps its copy private to the logging module; see the
    upstream ticket to have it export the scrub itself.
    """
    return _URL_RE.sub(lambda match: safe_endpoint(match.group(0)) or "***", text)


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

    return launch_maintenance_command(
        command, log_prefix="omop-emb", log_dir=log_dir
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
            registry_index_type=enum_value(record.index_type),
            registry_metric=enum_value(record.metric_type),
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
    return f"DROP INDEX IF EXISTS {quote_identifier(engine, index_name)};"


def _qualified_name(engine: Engine, schema: str | None, table: str) -> str:
    if not schema or (engine.dialect.name == "sqlite" and schema == "main"):
        return quote_identifier(engine, table)
    return f"{quote_identifier(engine, schema)}.{quote_identifier(engine, table)}"


def _canonical_model_name(model_name: str, *, provider_kind: str) -> str:
    return canonical_model_name(provider_kind, model_name)


