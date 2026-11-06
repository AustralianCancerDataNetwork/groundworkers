"""Connection policy for serving existing CDM databases."""

from pathlib import Path

from oa_configurator import ConfigurationError, ResolvedCDMDatabase, ResolvedVectorStore
from omop_alchemy.config import create_cdm_engines
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import NoSuchModuleError


def validate_duckdb_embedding_file(
    store: ResolvedVectorStore, *, cdm: ResolvedCDMDatabase | None = None
) -> None:
    """Require a prebuilt file instead of the upstream reader's empty fallback."""
    if store.backend_type != "duckdbvss":
        return
    database = make_url(store.database.connection.url).database
    if database in (None, "", ":memory:"):
        raise ConfigurationError(
            f"DuckDB embeddings require an existing regular file, not {database!r}."
        )
    # Match the upstream reader's literal path lookup. Expanding '~' only here
    # would validate a file that the reader then replaces with an empty store.
    path = Path(database).resolve()
    if not path.is_file():
        raise ConfigurationError(
            f"DuckDB embedding file is missing or is not a regular file: {path}"
        )
    if cdm is not None and any(
        connection.dialect_name == "duckdb"
        and Path(make_url(connection.url).database or "").expanduser().resolve() == path
        for connection in (cdm.connection, cdm.vocab_connection)
    ):
        raise ConfigurationError(
            "DuckDB embeddings must use a separate prebuilt file from the CDM."
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
    try:
        return resolved.create_engines(
            register_claims=False, connect_args={"read_only": True}
        )
    except (ImportError, NoSuchModuleError) as exc:
        raise ValueError(
            'Install "groundworkers[duckdb]" to serve DuckDB files.'
        ) from exc
