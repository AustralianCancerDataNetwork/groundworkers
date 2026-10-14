"""SQLAlchemy test engines routed through OMOP 2.0 role tags."""

from sqlalchemy.engine import Engine


def route_sqlite_omop_roles(engine: Engine) -> Engine:
    """Route OMOP roles to SQLite's default database for lightweight tests."""

    return engine.execution_options(
        schema_translate_map={
            "primary": None,
            "vocab": None,
            "results": None,
        }
    )
