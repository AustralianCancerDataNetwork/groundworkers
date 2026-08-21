"""SQL helpers shared across the setup and adapter layers."""

from __future__ import annotations

from sqlalchemy.engine import Connection, Engine

__all__ = ["quote_identifier"]


def quote_identifier(bind: Engine | Connection, name: str) -> str:
    """Quote *name* as an identifier for whatever dialect *bind* speaks.

    Delegates to SQLAlchemy's own identifier preparer rather than wrapping the
    value in double quotes, which is correct only for dialects that use them.
    """
    return bind.dialect.identifier_preparer.quote(name)
