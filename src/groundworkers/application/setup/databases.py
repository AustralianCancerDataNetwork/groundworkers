"""
This file holds the logic for resolving and verifying database targets for Groundworkers.
It includes functions to resolve database targets from a stack snapshot, verify connectivity and liveness of those targets, and perform diagnostics on the database schema and configuration.
It also includes logic to classify connection errors and provide guidance for remediation.
"""

from __future__ import annotations

import errno
import logging
import socket
import time
from collections.abc import Callable, Mapping
from typing import Any

from oa_configurator import (  # type: ignore[import-untyped]
    ResolvedCDMDatabase,
    Resolver,
    Role,
    physical_schema_of,
    supports_schemas,
)
from omop_alchemy.config import create_cdm_engines
from sqlalchemy import (
    Column,
    MetaData,
    Table,
    create_engine,
    exists,
    func,
    inspect,
    literal,
    select,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import NoSuchModuleError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from groundworkers.application.setup.models import (
    ClassifiedFailure,
    ConfigurationSnapshot,
    ConnectionFailureKind,
    ConnectionResult,
    DatabaseTarget,
    DiagnosticSeverity,
    ResourceDiagnostic,
)
from groundworkers.base.errors import embedding_store_error
from groundworkers.base.sql import (
    normalization_key_expression_sql,
)
from groundworkers.config import GroundworkersConfig

CDM_TABLES = (
    "concept",
    "concept_relationship",
    "concept_ancestor",
    "concept_synonym",
    "relationship",
)
logger = logging.getLogger(__name__)
GRAPH_TABLES = ("relationship_class", "relationship_mapping")
GRAPH_FTS_COLUMNS = {
    "concept": "concept_name_tsvector",
    "concept_synonym": "concept_synonym_name_tsvector",
}
GRAPH_FTS_INDEXES = (
    "idx_gin_concept_name_tsvector",
    "idx_gin_concept_synonym_name_tsvector",
)
GRAPH_FUNCTIONAL_INDEX_TARGETS = (
    (
        "concept",
        "concept_name",
        "concept.lower(concept_name)",
        (
            "ix_concept_concept_name_lower",
            "idx_concept_lower_name",
        ),
    ),
    (
        "concept_synonym",
        "concept_synonym_name",
        "concept_synonym.lower(concept_synonym_name)",
        (
            "ix_concept_synonym_concept_synonym_name_lower",
            "idx_concept_synonym_lower_name",
        ),
    ),
)
GROUNDWORKERS_TRIGRAM_INDEX_TARGETS = (
    (
        "concept",
        "concept_name",
        "concept.lower(concept_name) trigram",
        ("idx_concept_lower_name_trgm",),
    ),
    (
        "concept_synonym",
        "concept_synonym_name",
        "concept_synonym.lower(concept_synonym_name) trigram",
        ("idx_concept_synonym_lower_name_trgm",),
    ),
)
# (table, column, label, index name). The indexed K expression is not restated
# here: it is rendered from base.sql.normalization_key_expression, the same
# builder search_normalized queries with, so it cannot drift.
GROUNDWORKERS_NORMALIZED_INDEX_TARGETS = (
    (
        "concept",
        "concept_name",
        "concept normalized name",
        "idx_concept_name_normkey",
    ),
    (
        "concept_synonym",
        "concept_synonym_name",
        "concept_synonym normalized name",
        "idx_concept_synonym_name_normkey",
    ),
)
def resolve_database_targets(
    snapshot: ConfigurationSnapshot,
) -> tuple[DatabaseTarget, ...]:
    """Resolve safe connection targets from a usable stack snapshot."""

    if not snapshot.usable or snapshot.stack is None:
        return ()
    stack = snapshot.stack
    resolver = Resolver(stack)
    groundworkers = GroundworkersConfig.validate_candidate(stack)
    cdm = resolver.resolve_database(groundworkers.cdm_db)
    if not isinstance(cdm, ResolvedCDMDatabase):
        return ()
    schema_capable = supports_schemas(cdm.connection.dialect_name)
    cdm_schema = cdm.schema_for_role(Role.PRIMARY) if schema_capable else None
    vocab_schema = cdm.schema_for_role(Role.VOCAB) if schema_capable else None
    embedding_target: DatabaseTarget | None = None
    expected_embedding_model_name: str | None = None

    if groundworkers.embedding_model_name is not None:
        expected_embedding_model_name = resolver.resolve_model(
            groundworkers.embedding_model_name
        ).model
    if groundworkers.vector_store_name is not None:
        embedding = resolver.resolve_vector_store(groundworkers.vector_store_name)
        embedding_target = DatabaseTarget(
            key="database.embedding",
            label="Embedding store",
            database_entry_name=embedding.database.name,
            connection_name=embedding.database.connection.name,
            safe_url=embedding.database.connection.safe_url,
            cdm_schema=None,
            vocabulary_schema=None,
            connection_url=embedding.database.connection.url,
            role="embedding",
            resolved_vector_store=embedding,
        )

    targets = [
        DatabaseTarget(
            key="database.cdm",
            label="CDM / vocabulary",
            database_entry_name=cdm.name,
            connection_name=cdm.connection.name,
            safe_url=cdm.connection.safe_url,
            cdm_schema=cdm_schema,
            vocabulary_schema=vocab_schema,
            connection_url=cdm.connection.url,
            role="cdm",
            resolved_cdm_database=cdm,
        ),
        DatabaseTarget(
            key="database.graph",
            label="Graph readiness",
            database_entry_name=cdm.name,
            connection_name=cdm.connection.name,
            safe_url=cdm.connection.safe_url,
            cdm_schema=cdm_schema,
            vocabulary_schema=vocab_schema,
            connection_url=cdm.connection.url,
            role="graph",
            resolved_cdm_database=cdm,
        ),
        DatabaseTarget(
            key="database.groundworkers",
            label="Groundworkers tuning",
            database_entry_name=cdm.name,
            connection_name=cdm.connection.name,
            safe_url=cdm.connection.safe_url,
            cdm_schema=cdm_schema,
            vocabulary_schema=vocab_schema,
            connection_url=cdm.connection.url,
            role="groundworkers",
            expected_embedding_model_name=expected_embedding_model_name,
            resolved_vector_store=(
                embedding_target.resolved_vector_store
                if embedding_target is not None
                else None
            ),
            resolved_cdm_database=cdm,
        ),
    ]
    if embedding_target is not None:
        targets.append(embedding_target)
    return tuple(targets)


def verify_database_target(
    target: DatabaseTarget,
    *,
    engine_factory: Callable[[str], Engine] = create_engine,
    clock: Callable[[], float] = time.perf_counter,
) -> ConnectionResult:
    """Execute a bounded read-only liveness query against one target."""

    if (
        target.resolved_cdm_database is not None
        and target.role in {"cdm", "graph", "groundworkers"}
        and engine_factory is create_engine
    ):
        return _verify_cdm_target(target, clock=clock)
    engine: Engine | None = None
    started = clock()
    try:
        engine = engine_factory(target.connection_url)
        with engine.connect() as connection:
            connection.execute(select(literal(1)))
            diagnostics = _diagnostics_for_target(connection, connection, target)
    except Exception as exc:
        # Broad except: returned as a redacted category.
        return ConnectionResult(
            target_key=target.key,
            connected=False,
            latency_ms=None,
            safe_url=target.safe_url,
            failure=classify_connection_error(exc),
        )
    finally:
        if engine is not None:
            engine.dispose()
    return ConnectionResult(
        target_key=target.key,
        connected=True,
        latency_ms=round((clock() - started) * 1000, 1),
        safe_url=target.safe_url,
        diagnostics=tuple(diagnostics),
    )


def _verify_cdm_target(
    target: DatabaseTarget,
    *,
    clock: Callable[[], float],
) -> ConnectionResult:
    engines: tuple[Engine, Engine] | None = None
    started = clock()
    try:
        resolved_cdm_database = target.resolved_cdm_database
        if resolved_cdm_database is None:
            raise ValueError("CDM database configuration is missing")
        engines = create_cdm_engines(
            resolved_cdm_database, register_claims=False
        )
        primary_engine, vocab_engine = engines
        with primary_engine.connect() as primary, vocab_engine.connect() as vocab:
            try:
                primary.execute(select(literal(1)))
            except Exception as exc:
                raise RuntimeError("Primary database role is unavailable") from exc
            try:
                vocab.execute(select(literal(1)))
            except Exception as exc:
                raise RuntimeError("Vocabulary database role is unavailable") from exc
            diagnostics = _diagnostics_for_target(primary, vocab, target)
    except Exception as exc:
        logger.warning("Database target %s verification failed", target.key, exc_info=True)
        failure = classify_connection_error(exc)
        lowered_chain = " ".join(str(item).lower() for item in _exception_chain(exc))
        role_label = next(
            (
                role
                for role in ("vocabulary", "primary")
                if f"{role} database role is unavailable" in lowered_chain
            ),
            None,
        )
        if role_label is not None:
            failure = _failure(
                failure.kind,
                f"The {role_label} database role is unavailable. {failure.detail}",
                f"Check the {role_label} database connection. {failure.next_action}",
            )
        return ConnectionResult(
            target_key=target.key,
            connected=False,
            latency_ms=None,
            safe_url=target.safe_url,
            failure=failure,
        )
    finally:
        if engines is not None:
            seen: set[int] = set()
            for engine in engines:
                if id(engine) not in seen:
                    seen.add(id(engine))
                    engine.dispose()
    return ConnectionResult(
        target_key=target.key,
        connected=True,
        latency_ms=round((clock() - started) * 1000, 1),
        safe_url=target.safe_url,
        diagnostics=tuple(diagnostics),
    )


def _diagnostics_for_target(
    primary: Connection,
    vocab: Connection,
    target: DatabaseTarget,
) -> tuple[ResourceDiagnostic, ...]:
    if target.role == "embedding":
        return _embedding_diagnostics(target)
    if target.role == "graph":
        return _graph_diagnostics(
            primary, vocab, target.cdm_schema, target.vocabulary_schema
        )
    if target.role == "groundworkers":
        return _groundworkers_diagnostics(
            vocab, target
        )
    if target.role == "cdm":
        return _cdm_diagnostics(vocab, target.vocabulary_schema)
    return ()


def _cdm_diagnostics(
    connection: Connection,
    schema: str | None,
) -> tuple[ResourceDiagnostic, ...]:
    schema = physical_schema_of(connection, schema_tag=Role.VOCAB)
    inspector = inspect(connection)
    existing = set(inspector.get_table_names(schema=schema))
    missing = tuple(table for table in CDM_TABLES if table not in existing)
    if missing:
        return (
            _warning(
                "cdm_tables_missing",
                f"Connected, but the OMOP vocabulary tables are missing from schema {schema!r}: {', '.join(missing)}.",
            ),
        )
    return (
        _info(
            "cdm_tables_present",
            f"OMOP vocabulary tables are present in schema {schema!r}.",
        ),
    )


def _graph_diagnostics(
    primary: Connection,
    vocab: Connection,
    cdm_schema: str | None,
    vocabulary_schema: str | None,
) -> tuple[ResourceDiagnostic, ...]:
    cdm_schema = physical_schema_of(primary, schema_tag=Role.PRIMARY)
    vocabulary_schema = physical_schema_of(vocab, schema_tag=Role.VOCAB)
    diagnostics: list[ResourceDiagnostic] = list(
        _cdm_diagnostics(vocab, vocabulary_schema)
    )
    primary_inspector = inspect(primary)
    vocab_inspector = inspect(vocab)
    existing_tables = set(primary_inspector.get_table_names(schema=cdm_schema))
    vocabulary_tables = set(vocab_inspector.get_table_names(schema=vocabulary_schema))
    missing_graph_tables = tuple(
        table for table in GRAPH_TABLES if table not in existing_tables
    )
    if missing_graph_tables:
        diagnostics.append(
            _warning(
                "graph_tables_missing",
                "Graph relationship-classification tables are missing from "
                f"schema {cdm_schema!r}: {', '.join(missing_graph_tables)}.",
            )
        )
    else:
        diagnostics.append(
            _info(
                "graph_tables_present",
                f"Graph relationship-classification tables are present in schema {cdm_schema!r}.",
            )
        )
        empty_tables = tuple(
            table
            for table in GRAPH_TABLES
            if _table_is_empty(
                primary, cdm_schema, table, schema_tag=Role.PRIMARY
            )
        )
        if empty_tables:
            diagnostics.append(
                _warning(
                    "graph_tables_empty",
                    "Graph relationship-classification tables are empty: "
                    f"{', '.join(empty_tables)}.",
                )
            )

    if vocab.dialect.name == "postgresql":
        fulltext_sidecar_unpopulated: list[str] = []
        for table, column in GRAPH_FTS_COLUMNS.items():
            if table not in vocabulary_tables:
                continue
            columns = _column_names(vocab, vocabulary_schema, table)
            if column not in columns:
                diagnostics.append(
                    _warning(
                        "fulltext_sidecar_missing",
                        f"Full-text sidecar column {table}.{column} is missing.",
                    )
                )
            elif _column_has_null_values(
                vocab,
                vocabulary_schema,
                table,
                column,
                schema_tag=Role.VOCAB,
            ):
                fulltext_sidecar_unpopulated.append(f"{table}.{column}")
        if fulltext_sidecar_unpopulated:
            diagnostics.append(
                _warning(
                    "fulltext_sidecar_unpopulated",
                    "Full-text sidecar values are NULL for: "
                    + ", ".join(fulltext_sidecar_unpopulated)
                    + ". Run full-text population before relying on lexical grounding.",
                )
            )
        missing_fts_indexes = tuple(
            name
            for name in GRAPH_FTS_INDEXES
            if not _index_exists(vocab_inspector, vocabulary_schema, name)
        )
        if missing_fts_indexes:
            diagnostics.append(
                _warning(
                    "fulltext_indexes_missing",
                    f"Full-text GIN indexes are missing: {', '.join(missing_fts_indexes)}.",
                )
            )
        else:
            diagnostics.append(
                _info("fulltext_indexes_present", "Full-text GIN indexes are present.")
            )

        missing_functional = tuple(
            label
            for table, column, label, aliases in GRAPH_FUNCTIONAL_INDEX_TARGETS
            if table in vocabulary_tables
            and not _functional_lower_index_exists(
                vocab,
                vocab_inspector,
                vocabulary_schema,
                table,
                column,
                aliases,
            )
        )
        if missing_functional:
            diagnostics.append(
                _warning(
                    "functional_indexes_missing",
                    "Functional lower-name indexes are missing: "
                    f"{', '.join(missing_functional)}.",
                )
            )
        elif not vocabulary_tables:
            diagnostics.append(
                _warning(
                    "functional_indexes_unchecked",
                    "Functional text indexes could not be checked because vocabulary tables were not found.",
                )
            )
        else:
            diagnostics.append(
                _info("functional_indexes_present", "Functional text indexes are present.")
            )

    return tuple(diagnostics)


def _groundworkers_diagnostics(
    connection: Connection,
    target: DatabaseTarget,
) -> tuple[ResourceDiagnostic, ...]:
    diagnostics: list[ResourceDiagnostic] = []
    schema = physical_schema_of(connection, schema_tag=Role.VOCAB)
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names(schema=schema))
    missing_trigram = tuple(
        label
        for table, column, label, aliases in GROUNDWORKERS_TRIGRAM_INDEX_TARGETS
        if table in existing_tables
        and not _trigram_lower_index_exists(
            connection,
            inspector,
            schema,
            table,
            column,
            aliases,
        )
    )
    if connection.dialect.name == "postgresql" and missing_trigram:
        diagnostics.append(
            _warning(
                "trigram_indexes_missing",
                "Optional partial-match trigram indexes are missing: "
                f"{', '.join(missing_trigram)}. "
                "Groundworkers can still run, but constrained substring fallback searches may be slower.",
            )
        )
    elif connection.dialect.name == "postgresql":
        present_trigram = tuple(
            label
            for table, column, label, aliases in GROUNDWORKERS_TRIGRAM_INDEX_TARGETS
            if table in existing_tables
            and _trigram_lower_index_exists(
                connection,
                inspector,
                schema,
                table,
                column,
                aliases,
            )
        )
        if present_trigram:
            diagnostics.append(
                _info(
                    "trigram_indexes_present",
                    "Optional partial-match trigram indexes are present: "
                    f"{', '.join(present_trigram)}.",
                )
            )
        else:
            diagnostics.append(
                _warning(
                    "trigram_indexes_unchecked",
                    "Groundworkers partial-match tuning could not be checked because the vocabulary tables were not found.",
                )
            )

    if connection.dialect.name == "postgresql":
        diagnostics.extend(
            _normalized_index_diagnostics(connection, schema, existing_tables)
        )

    diagnostics.extend(_groundworkers_embedding_model_diagnostics(target))
    return tuple(diagnostics)


def _groundworkers_embedding_model_diagnostics(
    target: DatabaseTarget,
) -> tuple[ResourceDiagnostic, ...]:
    expected_model_name = target.expected_embedding_model_name
    if not expected_model_name:
        return ()
    if target.resolved_vector_store is None:
        return (
            _warning(
                "grounding_embedding_model_unchecked",
                f"Groundworkers grounding model {expected_model_name!r} could not be checked because no pgvector embedding store is configured.",
            ),
        )

    store = None
    try:
        from omop_emb.backends import open_vector_store_reader

        store = open_vector_store_reader(target.resolved_vector_store)
        record = store.get_registered_model(model_name=expected_model_name)
    except Exception as exc:
        failure = classify_connection_error(exc)
        return (
            _warning(
                f"grounding_{failure.kind.value}",
                f"Groundworkers grounding model {expected_model_name!r} could not be checked. {failure.detail}",
            ),
        )
    finally:
        if store is not None:
            store.close()

    if record is not None:
        return (
            _info(
                "grounding_embedding_model_registered",
                f"Groundworkers grounding model {expected_model_name!r} is registered.",
            ),
        )
    return (
        _warning(
            "grounding_embedding_model_missing",
            f"Groundworkers grounding model {expected_model_name!r} is not registered in the embedding store.",
        ),
    )


def _embedding_diagnostics(target: DatabaseTarget) -> tuple[ResourceDiagnostic, ...]:
    if target.resolved_vector_store is None:
        return (
            _warning(
                "embedding_registry_unconfigured",
                "No omop-emb vector store is configured.",
            ),
        )
    store = None
    try:
        from omop_emb.backends import open_vector_store_reader

        store = open_vector_store_reader(target.resolved_vector_store)
        if not store.initialized:
            return (
                _warning(
                    "embedding_registry_missing",
                    "Connected, but the omop-emb model registry table is missing.",
                ),
            )
        records = store.get_registered_models()
        models = tuple(
            (record, store.get_embedding_count(model_name=record.model_name))
            for record in records
        )
    except Exception as exc:
        failure = classify_connection_error(exc)
        return (_warning(f"embedding_{failure.kind.value}", failure.detail),)
    finally:
        if store is not None:
            store.close()

    if not models:
        return (
            _warning(
                "embedding_registry_empty",
                "Connected, but no embedding models are registered.",
            ),
        )

    diagnostics: list[ResourceDiagnostic] = [
        _info(
            "embedding_registry_present",
            f"{len(models)} embedding model(s) are registered.",
        )
    ]
    for record, count in models:
        if count == 0:
            diagnostics.append(
                _warning(
                    "embedding_table_empty",
                    f"Model {record.model_name!r} has no embeddings.",
                )
            )
        else:
            diagnostics.append(
                _info(
                    "embedding_table_valid",
                    f"Model {record.model_name!r} has {count} embedding row(s).",
                )
            )
    return tuple(diagnostics)


def classify_connection_error(exc: BaseException) -> ClassifiedFailure:
    """Map provider and database exceptions to redacted operator guidance."""

    chain = tuple(_exception_chain(exc))
    store_failure = _embedding_store_failure(chain)
    if store_failure is not None:
        return store_failure
    lowered = " ".join(str(item).lower() for item in chain)
    sqlstate = next(
        (
            str(value)
            for item in chain
            for value in (
                getattr(item, "sqlstate", None),
                getattr(getattr(item, "orig", None), "sqlstate", None),
            )
            if value
        ),
        "",
    )

    if sqlstate == "3D000":
        return _failure(ConnectionFailureKind.DATABASE_MISSING, "The configured database does not exist.", "Check the database name in the stack configuration.")
    if sqlstate == "3F000":
        return _failure(ConnectionFailureKind.SCHEMA_MISSING, "A configured database schema does not exist.", "Check the schema names in the stack configuration.")
    if sqlstate == "42P01":
        return _failure(ConnectionFailureKind.TABLE_MISSING, "A required database table does not exist.", "Check the database schema and setup status.")
    oa_failure = _oa_configuration_failure(chain)
    if oa_failure is not None:
        return oa_failure

    if any(
        isinstance(item, (ModuleNotFoundError, NoSuchModuleError)) for item in chain
    ):
        return _failure(
            ConnectionFailureKind.DRIVER_MISSING,
            "The configured database driver is not installed.",
            "Install the driver required by the selected dialect.",
        )
    if any(isinstance(item, socket.gaierror) for item in chain) or any(
        phrase in lowered
        for phrase in (
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo",
        )
    ):
        return _failure(
            ConnectionFailureKind.DNS,
            "The host name could not be resolved.",
            "Check the host name and this machine's DNS/network access.",
        )
    if (
        any(isinstance(item, (TimeoutError, SQLAlchemyTimeoutError)) for item in chain)
        or "timed out" in lowered
    ):
        return _failure(
            ConnectionFailureKind.TIMEOUT,
            "The connection attempt timed out.",
            "Check network access, the port, and whether the service is responding.",
        )
    if (
        any(
            isinstance(item, ConnectionRefusedError)
            or getattr(item, "errno", None) == errno.ECONNREFUSED
            for item in chain
        )
        or "connection refused" in lowered
    ):
        return _failure(
            ConnectionFailureKind.REFUSED,
            "The host refused the connection.",
            "Check the host and port and confirm the service is running.",
        )
    if sqlstate.startswith("28") or any(
        phrase in lowered
        for phrase in (
            "authentication failed",
            "password authentication",
            'role "',
            "role '",
            "unauthorized",
            "forbidden",
            "no pg_hba.conf entry",
        )
    ):
        return _failure(
            ConnectionFailureKind.AUTHENTICATION,
            "The service rejected the configured user or credentials.",
            "Check the host, port, user and password; confirm the configured role exists on the database that answered.",
        )
    if any(
        phrase in lowered
        for phrase in ("database does not exist", "unknown database", "not found")
    ):
        return _failure(
            ConnectionFailureKind.DATABASE_MISSING,
            "A configured database, model, vector store, or provider entry was not found.",
            "Check the named stack entry and the Groundworkers reference to it.",
        )
    if any(
        phrase in lowered
        for phrase in ("syntax error", "undefined table", "no such table")
    ):
        return _failure(
            ConnectionFailureKind.QUERY,
            "The service was reached but the verification query failed.",
            "Check the configured database and schema mappings.",
        )
    logger.error(
        "Unclassified database verification failure",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _failure(
        ConnectionFailureKind.OTHER,
        "The connection check failed for an unclassified reason.",
        "Review the application log for the original exception.",
    )


def _oa_configuration_failure(
    chain: tuple[BaseException, ...],
) -> ClassifiedFailure | None:
    messages = {
        "UnregisteredSchemaTagError": "A model uses an unregistered database schema tag.",
        "CrossDatabaseStatementError": "A query attempted to join tables from separate databases.",
        "UnboundExecutionError": "A database query has no configured role binding.",
        "SchemaOwnershipError": "The configured schema belongs to another database role.",
        "SchemaRegistryOutdatedError": "The database schema registry needs migration.",
        "SchemaDriftError": "The configured database schema differs from its registered state.",
    }
    for item in chain:
        name = type(item).__name__
        if name not in messages:
            continue
        hint = (
            " Run 'omop-config acknowledge-schema-migration' after reviewing the schema change."
            if name in {"SchemaRegistryOutdatedError", "SchemaDriftError"}
            else " Check the stack database role and schema configuration."
        )
        return _failure(ConnectionFailureKind.QUERY, messages[name], messages[name] + hint)
    return None


def _embedding_store_failure(
    chain: tuple[BaseException, ...],
) -> ClassifiedFailure | None:
    kinds = {
        "LEGACY_REGISTRY": ConnectionFailureKind.LEGACY_REGISTRY,
        "MISPLACED_REGISTRY": ConnectionFailureKind.MISPLACED_REGISTRY,
        "MISSING_STORAGE_TABLE": ConnectionFailureKind.MISSING_STORAGE_TABLE,
        "MODEL_REGISTRATION_CONFLICT": ConnectionFailureKind.MODEL_REGISTRATION_CONFLICT,
        "READ_ONLY_STORE": ConnectionFailureKind.READ_ONLY_STORE,
    }
    for item in chain:
        translated = embedding_store_error(item)
        if translated is not None:
            return _failure(
                kinds[translated.code], translated.message, translated.message
            )
    return None


def _failure(
    kind: ConnectionFailureKind, detail: str, next_action: str
) -> ClassifiedFailure:
    return ClassifiedFailure(kind=kind, detail=detail, next_action=next_action)


def _info(code: str, message: str) -> ResourceDiagnostic:
    return ResourceDiagnostic(
        code=code, message=message, severity=DiagnosticSeverity.INFO
    )


def _warning(code: str, message: str) -> ResourceDiagnostic:
    return ResourceDiagnostic(
        code=code, message=message, severity=DiagnosticSeverity.WARNING
    )


def _index_exists(inspector: Any, schema: str | None, index_name: str) -> bool:
    for table_name in CDM_TABLES + GRAPH_TABLES:
        try:
            indexes = inspector.get_indexes(table_name, schema=schema)
        except Exception:
            # Broad except: absence is reported as a warning.
            continue
        if any(index.get("name") == index_name for index in indexes):
            return True
    return False


def _functional_lower_index_exists(
    connection: Connection,
    inspector: Any,
    schema: str | None,
    table_name: str,
    column_name: str,
    aliases: tuple[str, ...],
) -> bool:
    if connection.dialect.name == "postgresql" and _postgres_lower_index_exists(
        connection,
        schema,
        table_name,
        column_name,
    ):
        return True
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
    except Exception:
        # Broad except: absence is reported as a warning.
        return False
    return any(index.get("name") in aliases for index in indexes)


def _normalized_index_diagnostics(
    connection: Connection,
    schema: str | None,
    existing_tables: set[str],
) -> tuple[ResourceDiagnostic, ...]:
    targets = tuple(
        target
        for target in GROUNDWORKERS_NORMALIZED_INDEX_TARGETS
        if target[0] in existing_tables
    )
    if not targets:
        return ()
    statuses = tuple(
        (label, normalized_index_status(connection, schema, table, column))
        for table, column, label, _name in targets
    )
    unchecked = tuple(label for label, present in statuses if present is None)
    if unchecked:
        return (
            _info(
                "normalized_indexes_unchecked",
                "Normalized-name indexes unchecked because PostgreSQL index listings "
                f"could not be read: {', '.join(unchecked)}.",
            ),
        )
    missing = tuple(label for label, present in statuses if not present)
    if missing:
        return (
            _warning(
                "normalized_indexes_missing",
                "Optional normalized-name indexes are missing or out of date: "
                f"{', '.join(missing)}. Groundworkers can still run, but unbounded "
                "normalized searches scan the whole vocabulary table.",
            ),
        )
    return (
        _info(
            "normalized_indexes_present",
            "Optional normalized-name indexes are present: "
            f"{', '.join(label for _table, _column, label, _name in targets)}.",
        ),
    )


def normalized_index_matches(
    connection: Connection,
    schema: str | None,
    table_name: str,
    column_name: str,
) -> bool:
    """True when an index on *table_name* covers the shared K expression."""
    return normalized_index_status(connection, schema, table_name, column_name) is True


def normalized_index_status(
    connection: Connection,
    schema: str | None,
    table_name: str,
    column_name: str,
) -> bool | None:
    """Return True/False for the K index, or None when listing failed."""
    indexes = _reflected_indexes(connection, schema, table_name)
    if indexes is None:
        return None
    expected = _squash_index_sql(
        normalization_key_expression_sql(connection, column_name)
    )
    return any(
        expected in _squash_index_sql(" ".join(index.get("expressions") or ()))
        for index in indexes
    )


def _squash_index_sql(sql: str) -> str:
    # pg_get_indexdef adds ::text casts and parentheses the compiled expression
    # lacks; drop both, plus whitespace and identifier quotes, on both sides.
    squashed = sql.lower().replace("::text", "")
    return "".join(ch for ch in squashed if ch not in ' \t\n"()')


def _trigram_lower_index_exists(
    connection: Connection,
    inspector: Any,
    schema: str | None,
    table_name: str,
    column_name: str,
    aliases: tuple[str, ...],
) -> bool:
    if connection.dialect.name == "postgresql":
        indexes = _reflected_indexes(connection, schema, table_name) or ()
        for index in indexes:
            options = index.get("dialect_options") or {}
            postgres_ops = options.get("postgresql_ops") or {}
            opclasses = postgres_ops.values() if isinstance(postgres_ops, dict) else ()
            using = options.get("postgresql_using")
            expression = " ".join(index.get("expressions") or ())
            if (
                str(using).lower() == "gin"
                and any("gin_trgm_ops" in str(op).lower() for op in opclasses)
                and _index_defines_lower_expression(expression, column_name)
            ):
                return True
        return False
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
    except Exception:
        # Broad except: absence is optional here.
        return False
    return any(index.get("name") in aliases for index in indexes)


def _postgres_lower_index_exists(
    connection: Connection,
    schema: str | None,
    table_name: str,
    column_name: str,
    *,
    required_fragment: str | None = None,
) -> bool:
    return any(
        _index_defines_lower_expression(
            " ".join(index.get("expressions") or ()),
            column_name,
            required_fragment=required_fragment,
        )
        for index in (_reflected_indexes(connection, schema, table_name) or ())
    )


def _reflected_indexes(
    connection: Connection,
    schema: str | None,
    table_name: str,
) -> tuple[Mapping[str, Any], ...] | None:
    try:
        indexes = inspect(connection).get_indexes(table_name, schema=schema)
    except Exception:
        # Callers fall back to reflected names or report absence.
        return None
    return tuple(indexes)


def _index_defines_lower_expression(
    indexdef: str,
    column_name: str,
    *,
    required_fragment: str | None = None,
) -> bool:
    normalised = "".join(indexdef.lower().replace('"', "").split())
    if required_fragment and required_fragment.lower() not in normalised:
        return False
    column = column_name.lower()
    return any(
        pattern in normalised
        for pattern in (
            f"lower(({column})::text)",
            f"lower({column}::text)",
            f"lower({column})",
        )
    )


def _column_names(
    connection: Connection,
    schema: str | None,
    table_name: str,
) -> set[str]:
    return {
        item["name"]
        for item in inspect(connection).get_columns(table_name, schema=schema)
    }


def _table_is_empty(
    connection: Connection,
    schema: str | None,
    table_name: str,
    *,
    schema_tag: Role,
) -> bool:
    return _count_table_rows(
        connection, schema, table_name, schema_tag=schema_tag
    ) == 0


def _column_has_null_values(
    connection: Connection,
    schema: str | None,
    table_name: str,
    column_name: str,
    *,
    schema_tag: Role,
) -> bool:
    """Return whether a full-text sidecar contains any unpopulated rows."""

    table = _role_table(connection, schema, table_name, schema_tag=schema_tag)
    return bool(
        connection.execute(
            select(
                exists(
                    select(literal(1))
                    .select_from(table)
                    .where(table.c[column_name].is_(None))
                )
            )
        ).scalar()
    )


def _count_table_rows(
    connection: Connection,
    schema: str | None,
    table_name: str,
    *,
    schema_tag: Role,
) -> int:
    table = _role_table(connection, schema, table_name, schema_tag=schema_tag)
    return int(
        connection.execute(select(func.count()).select_from(table)).scalar()
        or 0
    )


def _role_table(
    connection: Connection,
    schema: str | None,
    table_name: str,
    *,
    schema_tag: Role,
) -> Table:
    columns = inspect(connection).get_columns(table_name, schema=schema)
    table = Table(
        table_name,
        MetaData(),
        *(Column(column["name"], column["type"]) for column in columns),
        schema=schema_tag.value,
    )
    return table


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
