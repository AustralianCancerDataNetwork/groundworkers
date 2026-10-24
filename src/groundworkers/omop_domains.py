"""Domain routing facts that groundworkers owns and other components import.

The full set of OMOP ``domain_id`` values comes from the vocabulary's ``domain``
table (see :class:`groundworkers.base.domain_names.DomainNameResolver`); this module
does not repeat it. It owns two smaller facts the vocabulary does not record:

``DOMAIN_TO_TABLE``
    The CDM event table a record is written to, keyed by the ``domain_id`` of its
    selected standard target. The ``domain-routing`` knowledge pack publishes the
    same mapping to agents; tests keep the pack identical to this table.

``CLASSIFIABLE_DOMAINS`` and ``NON_CLINICAL``
    The output vocabulary of the ``domain_classify`` tool. The clinical values are
    a subset of the routed domains. The non-clinical values are *not* OMOP domains:
    they label data-dictionary fields that describe how data was collected
    (``Metadata``) or that only link records (``Identifier``). Callers decide what
    to do with them; groundcrew, for example, skips such fields.

This is published API: import from here rather than copying the values.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

DOMAIN_TO_TABLE: Final = MappingProxyType(
    {
        "Condition": "CONDITION_OCCURRENCE",
        "Drug": "DRUG_EXPOSURE",
        "Measurement": "MEASUREMENT",
        "Observation": "OBSERVATION",
        "Procedure": "PROCEDURE_OCCURRENCE",
        "Device": "DEVICE_EXPOSURE",
        "Specimen": "SPECIMEN",
        "Visit": "VISIT_OCCURRENCE",
        "Provider": "PROVIDER",
    }
)

CLASSIFIABLE_DOMAINS: Final[tuple[str, ...]] = (
    "Measurement",
    "Condition",
    "Observation",
    "Procedure",
    "Drug",
    "Device",
)

NON_CLINICAL: Final[tuple[str, ...]] = ("Metadata", "Identifier")

DOMAIN_CLASSIFY_OUTPUTS: Final[frozenset[str]] = frozenset(CLASSIFIABLE_DOMAINS + NON_CLINICAL)


def table_for_domain(domain_id: str | None) -> str | None:
    """The CDM table for a canonical ``domain_id``, or None when it is not routed.

    Expects the canonical spelling (``"Condition"``); normalise loosely typed input
    with ``DomainNameResolver.canonical`` first.
    """

    if domain_id is None:
        return None
    return DOMAIN_TO_TABLE.get(domain_id)
