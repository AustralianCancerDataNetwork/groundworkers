# CDM Adapter

`CDMAdapter` holds the primary engine, vocabulary engine, and routed session factory for a resolved CDM database. It is the shared connection layer used by `VocabService` and `OmopGraphAdapter`.

## What it is

`CDMAdapter` is a thin connection-management object. It does not issue queries itself. Its purpose is to:

- hold the primary and vocabulary `sqlalchemy.Engine` objects
- expose a routed session factory for callers that query role-tagged OMOP tables
- be constructed once per CDM database in `build_application()` and shared across the components that need it

## Who uses it

| Component | How |
|---|---|
| `VocabService` | Takes a `CDMAdapter` as its only constructor argument. Uses the session factory to open per-call sessions for vocabulary table queries. |
| `OmopGraphAdapter` | Shares the same engine (no separate connection pool) so omop-graph and vocabulary queries use the same connection pool. |

The adapter is created once per CDM database at application startup and wired through `build_application()`. It is not per-request.

## Construction

`CDMAdapter` is config-agnostic. It accepts an engine and optional routed factory, while `build_application()` creates them through `omop_alchemy.config.create_cdm_engines()` and `omop_alchemy.cross_database.cdm_sessionmaker()`:

- the adapter remains testable by passing a SQLite engine directly
- changing the connection source means changing `build_application()` only;`CDMAdapter` and every service that uses it are untouched

## Usage from `build_application()`

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

# CDMAdapter is available at:
app.adapters.cdm
```

`VocabService` is wired automatically:
```python
app.services.vocab  # backed by app.adapters.cdm
```

## Lifecycle

`build_app_config()` creates role engines through `create_cdm_engines(..., register_claims=False)`. On PostgreSQL this resolves schema claims by opening connections during startup, so an unreachable CDM prevents application startup with a configuration error that points operators to endpoints, credentials, schemas, and access. `CDMAdapter.is_available()` also performs a lightweight Core query (`SELECT 1`).

`close()` disposes both role engines. Call it explicitly in teardown (for example in a test fixture `yield`/`finally` block) when you need to ensure connections are returned promptly. Process-level startup and shutdown wiring belongs in the application or transport layer, not in the adapter itself.
