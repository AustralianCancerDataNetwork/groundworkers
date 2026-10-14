"""Canonical domain resolution, shared by the search and grounding paths.

Replaces a hardcoded six-name tuple that matched the set ``domain_classify``
emits. Inside that workflow it was correct; outside it, the other forty-four
domains only resolved when supplied in exactly the right case, and a wrong-cased
one filtered to zero results with no error.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from omop_alchemy.cdm.model.vocabulary import Domain
from sqlalchemy.orm import sessionmaker

from groundworkers.base.domain_names import DomainNameResolver
from tests.support.omop import route_sqlite_omop_roles


@pytest.fixture()
def resolver() -> DomainNameResolver:
    engine = route_sqlite_omop_roles(sa.create_engine("sqlite:///:memory:", future=True))
    Domain.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(Domain),
            [
                # domain_id and domain_name differ for most non-core domains --
                # the reason indexing only one of them is not enough.
                {"domain_id": "Condition", "domain_name": "Condition", "domain_concept_id": 19},
                {"domain_id": "Meas Value", "domain_name": "Measurement Value", "domain_concept_id": 24},
                {"domain_id": "Spec Anatomic Site", "domain_name": "Specimen Anatomic Site", "domain_concept_id": 38},
            ],
        )
    return DomainNameResolver(engine)


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("Condition", "Condition"),
        ("condition", "Condition"),
        ("CONDITION", "Condition"),
        ("  Condition  ", "Condition"),
        # The case that used to filter to zero: a non-core domain, wrong case.
        ("meas value", "Meas Value"),
        ("Meas Value", "Meas Value"),
        ("spec anatomic site", "Spec Anatomic Site"),
    ],
)
def test_domain_ids_resolve_regardless_of_case(resolver, supplied, expected):
    assert resolver.canonical(supplied) == expected


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("Measurement Value", "Meas Value"),
        ("measurement value", "Meas Value"),
        ("Specimen Anatomic Site", "Spec Anatomic Site"),
    ],
)
def test_domain_names_also_resolve_to_the_id(resolver, supplied, expected):
    """An agent reading the vocabulary catalogue is likely to type the name."""
    assert resolver.canonical(supplied) == expected


def test_unknown_domain_resolves_to_none(resolver):
    """None means "cannot exist", which the caller turns into INVALID_INPUT."""
    assert resolver.canonical("not a domain") is None


def test_no_domain_requested_is_not_an_unknown_domain(resolver):
    """None input means "no constraint" and must stay distinguishable."""
    assert resolver.canonical(None) is None


def test_known_domains_is_a_sorted_select_list(resolver):
    assert resolver.known_domains() == ("Condition", "Meas Value", "Spec Anatomic Site")


def test_unknown_message_names_the_value_and_valid_options(resolver):
    message = resolver.describe_unknown("bogus")

    assert "bogus" in message
    assert "Condition" in message


def test_missing_domain_table_on_engine_has_routing_guidance():
    engine = sa.create_engine("sqlite:///:memory:", future=True)  # no domain table

    resolver = DomainNameResolver(engine)

    with pytest.raises(RuntimeError, match="Engine mode cannot route vocabulary") as raised:
        resolver.canonical("Condition")
    assert raised.value.__cause__ is not None
    assert "no such table" in str(raised.value.__cause__).lower()


def test_engine_connection_errors_are_reraised_unchanged(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    resolver = DomainNameResolver(engine)
    original = RuntimeError("database is unavailable")

    def fail_connect():
        raise original

    monkeypatch.setattr(engine, "connect", fail_connect)
    with pytest.raises(RuntimeError, match="database is unavailable") as raised:
        resolver.canonical("Condition")
    assert raised.value is original


def test_domain_load_failure_is_retried_after_the_table_appears():
    engine = route_sqlite_omop_roles(sa.create_engine("sqlite:///:memory:", future=True))
    session_factory = sessionmaker(bind=engine)
    resolver = DomainNameResolver(session_factory)

    assert resolver.canonical("Condition") == "Condition"
    Domain.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.insert(Domain),
            {"domain_id": "Condition", "domain_name": "Condition", "domain_concept_id": 19},
        )

    assert resolver.canonical("condition") == "Condition"


def test_the_table_is_read_once(resolver):
    resolver.canonical("condition")
    engine = resolver._engine
    engine.dispose()  # a second read would now fail if the cache were not used

    assert resolver.canonical("meas value") == "Meas Value"
