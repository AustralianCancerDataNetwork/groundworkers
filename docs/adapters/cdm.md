# CDM Adapter

`CDMAdapter` holds the SQLAlchemy engine and session factory for a single CDM
database connection. It is the shared connection layer used by both `VocabService`
and `OmopGraphAdapter`.

## What it is

`CDMAdapter` is a thin connection-management object. It does not issue queries itself.
Its purpose is to:

- hold a `sqlalchemy.Engine` pointed at the CDM database
- expose a `sessionmaker` for callers that need scoped sessions
- be constructed once per CDM database in `build_application()` and shared across the
  components that need it

## Who uses it

| Component | How |
|---|---|
| `VocabService` | Takes a `CDMAdapter` as its only constructor argument. Uses the session factory to open per-call sessions for vocabulary table queries. |
| `OmopGraphAdapter` | Shares the same engine (no separate connection pool) so omop-graph and vocabulary queries use the same connection pool. |

The adapter is created once per CDM database at application startup and wired through
`build_application()`. It is not per-request.

## Construction

`CDMAdapter` is config-agnostic: it takes an already-constructed `sqlalchemy.Engine`,
not a config object. Only `build_application()` reads config and calls
`sqlalchemy.create_engine()`. This means:

- the adapter is testable by passing a SQLite engine or an in-memory engine directly
- migrating the connection source (e.g. to oa-configurator) means changing
  `build_application()` only; `CDMAdapter` and every service that uses it are untouched

## Usage from `build_application()`

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.load("config/groundworkers.local.yaml")
app = build_application(config)

# CDMAdapter is available at:
app.adapters.cdm
```

`VocabService` is wired automatically:
```python
app.services.vocab  # backed by app.adapters.cdm
```

## Lifecycle

`CDMAdapter` connects lazily: the engine is constructed at adapter creation time, but
the first database connection is deferred until the first query. `is_available()`
performs a lightweight probe (`SELECT 1`) to confirm connectivity without blocking
unrelated tool calls.

`close()` disposes the engine and releases the connection pool. Call it explicitly
in teardown (e.g. a test fixture `yield`/`finally` block) when you need to ensure
connections are returned promptly. The MCP server does not currently wire automatic
teardown.
