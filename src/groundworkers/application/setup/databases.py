"""
This file holds the logic for resolving and verifying database targets for Groundworkers.
It includes functions to resolve database targets from a stack snapshot, verify connectivity and liveness of those targets, and perform diagnostics on the database schema and configuration.
It also includes logic to classify connection errors and provide guidance for remediation.
"""

from __future__ import annotations

import errno
import socket
import time
from collections.abc import Callable
from typing import Any

from oa_configurator import (  # type: ignore[import-untyped]
    ResolvedCDMDatabase,
    Resolver,
)
from sqlalchemy import create_engine, inspect, text
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
from groundworkers.base.sql import quote_identifier
from groundworkers.config import GroundworkersConfig

CDM_TABLES = (
    "concept",
    "concept_relationship",
    "concept_ancestor",
    "concept_synonym",
    "relationship",
)
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
EMBEDDING_REGISTRY_TABLE = "model_registry"
EMBEDDING_REQUIRED_COLUMNS = (
    "concept_id",
    "domain_id",
    "vocabulary_id",
    "is_standard",
    "is_valid",
    "embedding",
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
    embedding_target: DatabaseTarget | None = None
    expected_embedding_model_name: str | None = None
    embedding_safe_url: str | None = None
    embedding_connection_url: str | None = None

    if groundworkers.embedding_model_name is not None:
        expected_embedding_model_name = resolver.resolve_model(
            groundworkers.embedding_model_name
        ).model
    if groundworkers.vector_store_name is not None:
        embedding = resolver.resolve_vector_store(groundworkers.vector_store_name)
        embedding_safe_url = embedding.database.connection.safe_url
        embedding_connection_url = embedding.database.connection.url
        embedding_schema = embedding.database.schema_name or "main"
        embedding_target = DatabaseTarget(
            key="database.embedding",
            label="Embedding store",
            database_entry_name=embedding.database.name,
            connection_name=embedding.database.connection.name,
            safe_url=embedding.database.connection.safe_url,
            cdm_schema=embedding_schema,
            vocabulary_schema=embedding_schema,
            connection_url=embedding.database.connection.url,
            role="embedding",
        )

    targets = [
        DatabaseTarget(
            key="database.cdm",
            label="CDM / vocabulary",
            database_entry_name=cdm.name,
            connection_name=cdm.connection.name,
            safe_url=cdm.connection.safe_url,
            cdm_schema=cdm.schema_name or "main",
            vocabulary_schema=cdm.vocab_schema,
            connection_url=cdm.connection.url,
            role="cdm",
        ),
        DatabaseTarget(
            key="database.graph",
            label="Graph readiness",
            database_entry_name=cdm.name,
            connection_name=cdm.connection.name,
            safe_url=cdm.connection.safe_url,
            cdm_schema=cdm.schema_name or "main",
            vocabulary_schema=cdm.vocab_schema,
            connection_url=cdm.connection.url,
            role="graph",
        ),
        DatabaseTarget(
            key="database.groundworkers",
            label="Groundworkers tuning",
            database_entry_name=cdm.name,
            connection_name=cdm.connection.name,
            safe_url=cdm.connection.safe_url,
            cdm_schema=cdm.schema_name or "main",
            vocabulary_schema=cdm.vocab_schema,
            connection_url=cdm.connection.url,
            role="groundworkers",
            expected_embedding_model_name=expected_embedding_model_name,
            embedding_safe_url=embedding_safe_url,
            embedding_connection_url=embedding_connection_url,
        ),
    ]
    if cdm.vocab_connection.name != cdm.connection.name:
        targets.append(
            DatabaseTarget(
                key="database.vocabulary",
                label="Vocabulary",
                database_entry_name=cdm.name,
                connection_name=cdm.vocab_connection.name,
                safe_url=cdm.vocab_connection.safe_url,
                cdm_schema=cdm.schema_name or "main",
                vocabulary_schema=cdm.vocab_schema,
                connection_url=cdm.vocab_connection.url,
                role="vocabulary",
            )
        )

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

    engine: Engine | None = None
    started = clock()
    try:
        engine = engine_factory(target.connection_url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            diagnostics = _diagnostics_for_target(
                connection,
                target,
                engine_factory=engine_factory,
            )
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


def _diagnostics_for_target(
    connection: Connection,
    target: DatabaseTarget,
    *,
    engine_factory: Callable[[str], Engine],
) -> tuple[ResourceDiagnostic, ...]:
    if target.role == "embedding":
        return _embedding_diagnostics(connection)
    if target.role == "graph":
        return _graph_diagnostics(connection, target.vocabulary_schema)
    if target.role == "groundworkers":
        return _groundworkers_diagnostics(
            connection,
            target,
            engine_factory=engine_factory,
        )
    if target.role in {"cdm", "vocabulary"}:
        return _cdm_diagnostics(connection, target.vocabulary_schema)
    return ()


def _cdm_diagnostics(
    connection: Connection,
    schema: str,
) -> tuple[ResourceDiagnostic, ...]:
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
    connection: Connection,
    schema: str,
) -> tuple[ResourceDiagnostic, ...]:
    diagnostics: list[ResourceDiagnostic] = list(_cdm_diagnostics(connection, schema))
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names(schema=schema))
    missing_graph_tables = tuple(
        table for table in GRAPH_TABLES if table not in existing_tables
    )
    if missing_graph_tables:
        diagnostics.append(
            _warning(
                "graph_tables_missing",
                "Graph relationship-classification tables are missing from "
                f"schema {schema!r}: {', '.join(missing_graph_tables)}.",
            )
        )
    else:
        diagnostics.append(
            _info(
                "graph_tables_present",
                f"Graph relationship-classification tables are present in schema {schema!r}.",
            )
        )
        empty_tables = tuple(
            table
            for table in GRAPH_TABLES
            if _table_is_empty(connection, schema, table)
        )
        if empty_tables:
            diagnostics.append(
                _warning(
                    "graph_tables_empty",
                    "Graph relationship-classification tables are empty: "
                    f"{', '.join(empty_tables)}.",
                )
            )

    for table, column in GRAPH_FTS_COLUMNS.items():
        if table not in existing_tables:
            continue
        columns = _column_names(connection, schema, table)
        if column not in columns:
            diagnostics.append(
                _warning(
                    "fulltext_sidecar_missing",
                    f"Full-text sidecar column {table}.{column} is missing.",
                )
            )
    missing_fts_indexes = tuple(
        name for name in GRAPH_FTS_INDEXES if not _index_exists(inspector, schema, name)
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
        if table in existing_tables
        and not _functional_lower_index_exists(
            connection,
            inspector,
            schema,
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
    else:
        diagnostics.append(
            _info("functional_indexes_present", "Functional text indexes are present.")
        )

    return tuple(diagnostics)


def _groundworkers_diagnostics(
    connection: Connection,
    target: DatabaseTarget,
    *,
    engine_factory: Callable[[str], Engine],
) -> tuple[ResourceDiagnostic, ...]:
    diagnostics: list[ResourceDiagnostic] = []
    schema = target.vocabulary_schema
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
    if missing_trigram:
        diagnostics.append(
            _warning(
                "trigram_indexes_missing",
                "Optional partial-match trigram indexes are missing: "
                f"{', '.join(missing_trigram)}. "
                "Groundworkers can still run, but constrained substring fallback searches may be slower.",
            )
        )
    else:
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

    diagnostics.extend(
        _groundworkers_embedding_model_diagnostics(
            target,
            engine_factory=engine_factory,
        )
    )
    return tuple(diagnostics)


def _groundworkers_embedding_model_diagnostics(
    target: DatabaseTarget,
    *,
    engine_factory: Callable[[str], Engine],
) -> tuple[ResourceDiagnostic, ...]:
    expected_model_name = target.expected_embedding_model_name
    if not expected_model_name:
        return ()
    if not target.embedding_connection_url:
        return (
            _warning(
                "grounding_embedding_model_unchecked",
                f"Groundworkers grounding model {expected_model_name!r} could not be checked because no pgvector embedding store is configured.",
            ),
        )

    engine: Engine | None = None
    try:
        engine = engine_factory(target.embedding_connection_url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            inspector = inspect(connection)
            existing_tables = set(inspector.get_table_names())
            if EMBEDDING_REGISTRY_TABLE not in existing_tables:
                return (
                    _warning(
                        "grounding_embedding_registry_missing",
                        "Groundworkers grounding model could not be checked because the omop-emb model registry table is missing.",
                    ),
                )
            rows = _embedding_registry_rows(connection)
    except Exception as exc:
        # Broad except: reported as a redacted warning.
        failure = classify_connection_error(exc)
        return (
            _warning(
                "grounding_embedding_model_unchecked",
                f"Groundworkers grounding model {expected_model_name!r} could not be checked. {failure.detail}",
            ),
        )
    finally:
        if engine is not None:
            engine.dispose()

    registered_model_names = {str(row["model_name"]) for row in rows}
    if expected_model_name in registered_model_names:
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


def _embedding_diagnostics(connection: Connection) -> tuple[ResourceDiagnostic, ...]:
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    if EMBEDDING_REGISTRY_TABLE not in existing_tables:
        return (
            _warning(
                "embedding_registry_missing",
                "Connected, but the omop-emb model registry table is missing.",
            ),
        )

    rows = _embedding_registry_rows(connection)
    if not rows:
        return (
            _warning(
                "embedding_registry_empty",
                "Connected, but no embedding models are registered.",
            ),
        )

    diagnostics: list[ResourceDiagnostic] = [
        _info(
            "embedding_registry_present",
            f"{len(rows)} embedding model(s) are registered.",
        )
    ]
    for row in rows:
        table_name = str(row["storage_identifier"])
        if table_name not in existing_tables:
            diagnostics.append(
                _warning(
                    "embedding_table_missing",
                    f"Storage table {table_name!r} for model {row['model_name']!r} is missing.",
                )
            )
            continue
        columns = _column_names(connection, None, table_name)
        missing_columns = tuple(
            column for column in EMBEDDING_REQUIRED_COLUMNS if column not in columns
        )
        if missing_columns:
            diagnostics.append(
                _warning(
                    "embedding_table_invalid",
                    f"Storage table {table_name!r} is missing columns: {', '.join(missing_columns)}.",
                )
            )
            continue
        count = _count_table_rows(connection, None, table_name)
        if count == 0:
            diagnostics.append(
                _warning(
                    "embedding_table_empty",
                    f"Storage table {table_name!r} for model {row['model_name']!r} has no embeddings.",
                )
            )
        else:
            diagnostics.append(
                _info(
                    "embedding_table_valid",
                    f"Model {row['model_name']!r} metadata is valid and {count} embedding row(s) are present.",
                )
            )
    return tuple(diagnostics)


def _embedding_registry_rows(connection: Connection):
    return (
        connection.execute(
            text(
                "SELECT model_name, storage_identifier, dimensions, index_type, metric_type "
                "FROM model_registry ORDER BY model_name"
            )
        )
        .mappings()
        .all()
    )


def classify_connection_error(exc: BaseException) -> ClassifiedFailure:
    """Map provider and database exceptions to redacted operator guidance."""

    chain = tuple(_exception_chain(exc))
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
            "does not exist",
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
    if sqlstate == "3D000" or any(
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
    return _failure(
        ConnectionFailureKind.OTHER,
        "The connection check failed for an unclassified reason.",
        "Review the application log for the original exception.",
    )


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


def _trigram_lower_index_exists(
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
        required_fragment="gin_trgm_ops",
    ):
        return True
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
    try:
        rows = connection.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = :schema
                  AND tablename = :table_name
                """
            ),
            {"schema": schema or "public", "table_name": table_name},
        )
    except Exception:
        # Broad except: fall back to reflected index names.
        return False
    return any(
        _index_defines_lower_expression(
            str(row[0]),
            column_name,
            required_fragment=required_fragment,
        )
        for row in rows
    )


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
    if connection.dialect.name == "postgresql":
        rows = connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name = :table_name
                """
            ),
            {"schema": schema or "public", "table_name": table_name},
        )
        return {str(row[0]) for row in rows}
    if connection.dialect.name == "sqlite":
        rows = connection.execute(
            text(f"PRAGMA table_info({quote_identifier(connection, table_name)})")
        )
        return {str(row[1]) for row in rows}
    return {
        item["name"]
        for item in inspect(connection).get_columns(table_name, schema=schema)
    }


def _table_is_empty(
    connection: Connection, schema: str | None, table_name: str
) -> bool:
    return _count_table_rows(connection, schema, table_name) == 0


def _count_table_rows(
    connection: Connection,
    schema: str | None,
    table_name: str,
) -> int:
    qualified = _qualified_name(connection, schema, table_name)
    return int(
        connection.execute(text(f"SELECT count(*) FROM {qualified}")).scalar() or 0
    )


def _qualified_name(connection: Connection, schema: str | None, table_name: str) -> str:
    if schema:
        return (
            f"{quote_identifier(connection, schema)}."
            f"{quote_identifier(connection, table_name)}"
        )
    return quote_identifier(connection, table_name)


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
