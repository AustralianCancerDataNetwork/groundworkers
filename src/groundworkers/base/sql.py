"""SQL helpers shared across the setup and adapter layers."""

from __future__ import annotations

from sqlalchemy.engine import Connection, Engine

__all__ = ["effective_schema", "quote_identifier"]


def quote_identifier(bind: Engine | Connection, name: str) -> str:
    """Quote *name* as an identifier for whatever dialect *bind* speaks.

    Delegates to SQLAlchemy's own identifier preparer rather than wrapping the
    value in double quotes, which is correct only for dialects that use them.
    """
    return bind.dialect.identifier_preparer.quote(name)


def effective_schema(bind: Engine | Connection) -> str | None:
    """The schema that schema-unqualified tables resolve to on *bind*.

    oa-configurator expresses schema placement entirely through SQLAlchemy's
    ``schema_translate_map`` and sets no ``search_path``. That map is applied
    when SQLAlchemy *compiles* a statement, so ORM and Core constructs are
    routed for free -- but reflection is not: ``Inspector`` takes a literal
    schema name and ignores the map completely. Anything that inspects rather
    than queries has to be told where to look, and this is where that answer
    comes from, so reflection and generated SQL cannot disagree.
    """
    return bind.get_execution_options().get("schema_translate_map", {}).get(None)
