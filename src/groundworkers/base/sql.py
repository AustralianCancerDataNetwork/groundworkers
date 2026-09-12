"""SQL helpers shared across the setup and adapter layers."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import func
from sqlalchemy.engine import Connection, Engine

__all__ = [
    "STOP_PHRASE_SQL_PATTERN",
    "NormalizationProfile",
    "effective_schema",
    "normalization_expression",
    "quote_identifier",
]


class NormalizationProfile(StrEnum):
    """Supported normalization contracts shared by Python and SQL callers."""

    VERBATIM = "verbatim"
    AGGRESSIVE = "aggressive"
    DRUG_NAME = "drug_name"


#: Stop phrases stripped before comparison, as a PostgreSQL regex.
STOP_PHRASE_SQL_PATTERN = r"\m(?:nos|nec|nfs|unspecified|unknown|other|w/?o|w/)\M"


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


def normalization_expression(
    column,
    *,
    profile: str | NormalizationProfile,
    remove_stop_phrases: bool,
):
    """Build the SQL expression used for normalized comparison."""
    profile = NormalizationProfile(profile)
    space = " "
    globally = "g"
    expr = func.lower(column)
    if remove_stop_phrases:
        expr = func.regexp_replace(expr, STOP_PHRASE_SQL_PATTERN, space, globally)
    expr = func.regexp_replace(expr, r"[^a-z0-9]+", space, globally)
    if profile == "drug_name":
        expr = func.replace(expr, " extended release ", space)
        expr = func.replace(expr, " modified release ", space)
    expr = func.regexp_replace(expr, r"\s+", space, globally)
    return func.btrim(expr)
