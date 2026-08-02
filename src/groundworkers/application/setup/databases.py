from __future__ import annotations

import errno
import socket
import time
from collections.abc import Callable
from typing import Any

from oa_configurator import Resolver
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchModuleError, TimeoutError as SQLAlchemyTimeoutError

from groundworkers.application.setup.models import (
    ClassifiedFailure,
    ConfigurationSnapshot,
    ConnectionFailureKind,
    ConnectionResult,
    DatabaseTarget,
)
from groundworkers.config import (
    resolve_cdm_resource_name,
    resolve_embedding_resource_name,
)


def resolve_database_targets(
    snapshot: ConfigurationSnapshot,
) -> tuple[DatabaseTarget, ...]:
    """Resolve safe connection targets from a usable stack snapshot."""

    if not snapshot.usable or snapshot.stack is None:
        return ()
    stack = snapshot.stack
    resolver = Resolver(stack)
    cdm_name = resolve_cdm_resource_name(stack)
    cdm = resolver.resolve_resource(cdm_name)
    targets = [
        DatabaseTarget(
            key="database.cdm",
            label="CDM / vocabulary",
            resource_name=cdm_name,
            database_name=cdm.database.name,
            safe_url=cdm.database.safe_url,
            cdm_schema=cdm.cdm_schema,
            vocabulary_schema=cdm.vocab_schema,
            connection_url=cdm.database.url,
        )
    ]
    if cdm.vocab_database.name != cdm.database.name:
        targets.append(
            DatabaseTarget(
                key="database.vocabulary",
                label="Vocabulary",
                resource_name=cdm_name,
                database_name=cdm.vocab_database.name,
                safe_url=cdm.vocab_database.safe_url,
                cdm_schema=cdm.cdm_schema,
                vocabulary_schema=cdm.vocab_schema,
                connection_url=cdm.vocab_database.url,
            )
        )

    embedding_tool = (
        resolver.resolve_tool("omop_emb") if _has_tool(stack, "omop_emb") else None
    )
    if embedding_tool and embedding_tool.extra.get("backend", "pgvector") == "pgvector":
        embedding_name = resolve_embedding_resource_name(stack)
        embedding = resolver.resolve_resource(embedding_name)
        targets.append(
            DatabaseTarget(
                key="database.embedding",
                label="Embedding store",
                resource_name=embedding_name,
                database_name=embedding.database.name,
                safe_url=embedding.database.safe_url,
                cdm_schema=embedding.cdm_schema,
                vocabulary_schema=embedding.vocab_schema,
                connection_url=embedding.database.url,
            )
        )
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
    except Exception as exc:  # noqa: BLE001 - returned as a redacted category
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
            "unauthorized",
            "forbidden",
        )
    ):
        return _failure(
            ConnectionFailureKind.AUTHENTICATION,
            "The service rejected the supplied credentials.",
            "Check the user, password or API key and its permissions.",
        )
    if sqlstate == "3D000" or any(
        phrase in lowered
        for phrase in ("database does not exist", "unknown database", "not found")
    ):
        return _failure(
            ConnectionFailureKind.DATABASE_MISSING,
            "The configured database or provider resource was not found.",
            "Check the database, model or resource name.",
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


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _has_tool(stack: Any, name: str) -> bool:
    if name in stack.tools:
        return True
    return bool(
        stack.active_profile
        and stack.active_profile in stack.profiles
        and name in stack.profiles[stack.active_profile].tools
    )
