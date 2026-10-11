"""SQL helpers shared across the setup and adapter layers."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import column as sql_column
from sqlalchemy import func, literal
from sqlalchemy.engine import Connection, Engine

__all__ = [
    "KEY_DROP_TOKENS",
    "KEY_DROP_TOKEN_PATTERN",
    "STOP_PHRASE_SQL_PATTERN",
    "NormalizationProfile",
    "effective_schema",
    "normalization_expression",
    "normalization_index_expression_sql",
    "normalization_key_expression",
    "normalization_key_expression_sql",
    "normalized_key",
    "quote_identifier",
]


class NormalizationProfile(StrEnum):
    """Supported normalization contracts shared by Python and SQL callers."""

    VERBATIM = "verbatim"
    AGGRESSIVE = "aggressive"
    DRUG_NAME = "drug_name"


#: Stop phrases stripped before comparison, as a PostgreSQL regex.
STOP_PHRASE_SQL_PATTERN = r"\m(?:nos|nec|nfs|unspecified|unknown|other|w/?o|w/)\M"

# Lossless coarse-key invariant: every normalization variant may only delete
# tokens from this set relative to lowercase + non-alphanumeric-to-space.
# Extending a variant beyond this set requires extending the K expression too.
KEY_DROP_TOKENS = frozenset(
    {
        "nos", "nec", "nfs", "unspecified", "unknown", "other", "w", "o",
        "wo", "extended", "modified", "release",
    }
)
KEY_DROP_TOKEN_PATTERN = "(?:" + "|".join(sorted(KEY_DROP_TOKENS)) + ")"


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
    """Build the SQL expression used for normalized comparison.

    The regex patterns and flags are rendered inline rather than bound: a
    prepared statement's generic plan cannot match an expression index whose
    constants arrive as parameters, so bound patterns silently force a full
    scan once the driver starts reusing statements. Search applies this exact
    comparison together with the shared lossless coarse-key expression.
    """
    profile = NormalizationProfile(profile)
    space = _inline(" ")
    globally = _inline("g")
    expr = func.lower(column)
    if remove_stop_phrases:
        expr = func.regexp_replace(
            expr, _inline(STOP_PHRASE_SQL_PATTERN), space, globally
        )
    expr = func.regexp_replace(expr, _inline(r"[^a-z0-9]+"), space, globally)
    if profile == "drug_name":
        expr = func.replace(expr, _inline(" extended release "), space)
        expr = func.replace(expr, _inline(" modified release "), space)
    expr = func.regexp_replace(expr, _inline(r"\s+"), space, globally)
    return func.btrim(expr)


def normalization_index_expression_sql(
    bind: Engine | Connection,
    column_name: str,
    *,
    profile: str | NormalizationProfile = NormalizationProfile.VERBATIM,
    remove_stop_phrases: bool = True,
) -> str:
    """Render :func:`normalization_expression` as index DDL for *bind*'s dialect.

    Compile against a connected dialect: literal escaping follows the server's
    ``standard_conforming_strings``, and a never-connected dialect doubles the
    regex backslashes, producing an index no query can match. The default
    arguments are ``search_normalized``'s defaults, the variant callers use.
    """
    expression = normalization_expression(
        sql_column(column_name),
        profile=profile,
        remove_stop_phrases=remove_stop_phrases,
    )
    return str(
        expression.compile(
            dialect=bind.dialect,
            compile_kwargs={"literal_binds": True},
        )
    )


def normalized_key(normalised_query: str) -> str:
    """Build the lossless coarse key for an already-normalized query."""
    return " ".join(
        token for token in normalised_query.split() if token not in KEY_DROP_TOKENS
    )


def normalization_key_expression_sql(
    bind: Engine | Connection, column_name: str
) -> str:
    """Render K(column), the shared coarse-key expression used by all variants."""
    expression = normalization_key_expression(sql_column(column_name))
    return str(
        expression.compile(
            dialect=bind.dialect,
            compile_kwargs={"literal_binds": True},
        )
    )


def normalization_key_expression(column):
    """Build K(column), which removes only tokens normalization may delete."""
    space = _inline(" ")
    globally = _inline("g")
    base = func.regexp_replace(
        func.lower(column), _inline(r"[^a-z0-9]+"), space, globally
    )
    padded = _inline(" ") + func.btrim(base) + _inline(" ")
    token_pattern = f" {KEY_DROP_TOKEN_PATTERN}(?= )"
    dropped = func.regexp_replace(padded, _inline(token_pattern), _inline(""), globally)
    collapsed = func.regexp_replace(dropped, _inline(r"\s+"), space, globally)
    expression = func.btrim(collapsed)
    return expression


def _inline(value: str):
    return literal(value, literal_execute=True)
