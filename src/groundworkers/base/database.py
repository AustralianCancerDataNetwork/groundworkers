"""Connection policy for serving existing CDM databases."""

from pathlib import Path

from oa_configurator import ConfigurationError, ResolvedCDMDatabase, ResolvedVectorStore
from omop_alchemy.config import create_cdm_engines
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import NoSuchModuleError


def duckdb_file_path(database: str | None) -> Path:
    """Use the configured absolute filename without resolving a relative base."""
    if not database or not Path(database).is_absolute():
        raise ConfigurationError(
            f"DuckDB database file paths must be absolute, not {database!r}. "
            "Set database_name to the full file path without ~."
        )
    return Path(database)


def same_database_file(first: Path, second: Path) -> bool:
    """Compare file identity, retaining path equality for a not-yet-created file."""
    return first.resolve() == second.resolve() or (
        first.is_file() and second.is_file() and first.samefile(second)
    )


def validate_duckdb_cdm_paths(resolved: ResolvedCDMDatabase) -> None:
    """Require absolute filenames for both configured DuckDB CDM roles."""
    for connection in (resolved.connection, resolved.vocab_connection):
        if connection.dialect_name == "duckdb":
            duckdb_file_path(make_url(connection.url).database)


def validate_duckdb_embedding_file(
    store: ResolvedVectorStore,
    *,
    cdm: ResolvedCDMDatabase | None = None,
    allow_create: bool = False,
) -> None:
    """Require a real file; only explicit initialization may create a new one."""
    if store.backend_type != "duckdbvss":
        return
    database = make_url(store.database.connection.url).database
    if database in (None, "", ":memory:"):
        raise ConfigurationError(
            f"DuckDB embeddings require an existing regular file, not {database!r}."
        )
    path = duckdb_file_path(database)
    if not path.is_file() and not (
        allow_create and not path.exists() and path.parent.is_dir()
    ):
        raise ConfigurationError(
            f"DuckDB embedding file is missing or is not a regular file: {path}"
        )
    if cdm is not None and any(
        connection.dialect_name == "duckdb"
        and same_database_file(
            duckdb_file_path(make_url(connection.url).database), path
        )
        for connection in (cdm.connection, cdm.vocab_connection)
    ):
        raise ConfigurationError(
            "DuckDB embeddings must use a separate file from the CDM."
        )


def create_runtime_cdm_engines(resolved: ResolvedCDMDatabase) -> tuple[Engine, Engine]:
    """Open a DuckDB CDM read-only; retain the existing policy for other dialects."""
    connections = (resolved.connection, resolved.vocab_connection)
    if not any(connection.dialect_name == "duckdb" for connection in connections):
        return create_cdm_engines(resolved, register_claims=False)
    if any(
        connection.dialect_name == "duckdb"
        and make_url(connection.url).database in (None, "", ":memory:")
        for connection in connections
    ):
        raise ValueError(
            "DuckDB serving requires an existing CDM file, not an in-memory database."
        )
    if not all(
        connection.dialect_name == "duckdb" for connection in connections
    ) or not (
        resolved.connection.addresses_same_database_as(resolved.vocab_connection)
    ):
        raise ValueError(
            "DuckDB serving requires primary and vocabulary roles in one file."
        )
    validate_duckdb_cdm_paths(resolved)
    try:
        return resolved.create_engines(
            register_claims=False, connect_args={"read_only": True}
        )
    except (ImportError, NoSuchModuleError) as exc:
        raise ValueError(
            'Install "groundworkers[duckdb]" to serve DuckDB files.'
        ) from exc
