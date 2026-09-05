"""The single place a concept becomes an MCP payload.

Groundworkers owns the wire shape and nothing else. Flag semantics belong to
omop-alchemy, the concept view to omop-graph, nearest-match results to omop-emb;
this module's whole job is translating those upstream types into one consistent
JSON shape.

Wire vocabulary
---------------
``standard_concept`` / ``classification_concept`` / ``is_active``, borrowed from
``omop_graph.graph.nodes.ConceptView`` — which in turn matches the OMOP column
name. Upstream types that use ``is_standard`` are translated here, at the
boundary, rather than leaking a second vocabulary to callers.

Detail levels
-------------
Additive projections of ``ConceptView``, not independent shapes:

``identity``
    Who the concept is: id, name, vocabulary, domain.
``flags``
    ``identity`` plus code, class, and the standardness/validity flags.
``full``
    ``flags`` plus validity dates and the raw ``invalid_reason``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

Detail = Literal["identity", "flags", "full"]


def _date_to_iso(value: date | str | None) -> str | None:
    """Render a date as ISO-8601, tolerating values already stringified."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def _identity(view: Any) -> dict[str, Any]:
    return {
        "concept_id": int(view.concept_id),
        "concept_name": view.concept_name,
        "vocabulary_id": view.vocabulary_id,
        "domain_id": view.domain_id,
    }


def serialise_concept_view(view: Any, *, detail: Detail = "flags") -> dict[str, Any]:
    """Project an omop-graph ``ConceptView`` onto the wire shape.

    Parameters
    ----------
    view:
        Anything with ``ConceptView``'s attributes. Typed as ``Any`` because
        omop-graph does not export the type for static use.
    detail:
        How much of the view to include. See the module docstring.
    """
    payload = _identity(view)
    if detail == "identity":
        return payload

    payload.update(
        {
            "concept_code": view.concept_code,
            "concept_class_id": view.concept_class_id,
            "standard_concept": bool(view.standard_concept),
            "classification_concept": bool(view.classification_concept),
            "is_active": bool(view.is_active),
        }
    )
    if detail == "flags":
        return payload

    payload.update(
        {
            "valid_start_date": _date_to_iso(view.valid_start_date),
            "valid_end_date": _date_to_iso(view.valid_end_date),
            "invalid_reason": view.invalid_reason,
        }
    )
    return payload


#: Keys contributed by each detail level, in wire order. ``full`` is the shape
#: :func:`serialise_concept_view` produces at maximum detail, so a payload built
#: there can always be projected back down to a narrower level.
_LEVEL_KEYS: dict[str, tuple[str, ...]] = {
    "identity": ("concept_id", "concept_name", "vocabulary_id", "domain_id"),
    "flags": (
        "concept_code",
        "concept_class_id",
        "standard_concept",
        "classification_concept",
        "is_active",
    ),
    "full": ("valid_start_date", "valid_end_date", "invalid_reason"),
}


def project_payload(payload: dict[str, Any], *, detail: Detail = "flags") -> dict[str, Any]:
    """Narrow an already-serialised concept payload to *detail*.

    Services receive concepts from the adapter already serialised at ``full``
    detail. Rather than re-listing fields — which is how
    ``classification_concept`` came to be missing from the hierarchy walk — they
    project the payload down to the level they publish. Keys absent from the
    source are omitted rather than invented.
    """
    wanted: list[str] = list(_LEVEL_KEYS["identity"])
    if detail in ("flags", "full"):
        wanted += _LEVEL_KEYS["flags"]
    if detail == "full":
        wanted += _LEVEL_KEYS["full"]
    return {key: payload[key] for key in wanted if key in payload}


def serialise_nearest_match(match: Any) -> dict[str, Any]:
    return {
        "concept_id": int(match.concept_id),
        "concept_name": getattr(match, "concept_name", None),
        "vocabulary_id": getattr(match, "vocabulary_id", None),
        "domain_id": getattr(match, "domain_id", None),
        "similarity": round(float(match.similarity), 6),
        "standard_concept": _optional_bool(match.is_standard),
        "classification_concept": _optional_bool(getattr(match, "is_classification", None)),
        "is_active": _optional_bool(match.is_active),
    }


def serialise_label_match(match: Any) -> dict[str, Any]:
    return {
        "concept_id": int(match.matched_concept_id),
        "matched_label": match.matched_concept_label,
        "match_kind": getattr(match.match_kind, "name", str(match.match_kind)),
        "standard_concept": _optional_bool(match.is_standard),
        # LabelMatch carries no classification signal — omop-graph's label
        # resolvers report the combined standardness flag, not the strict split.
        # None says "not available from this channel", not "false".
        "classification_concept": None,
        "is_active": _optional_bool(match.is_active),
        "synonym": bool(match.synonym),
    }


def _optional_bool(value: Any) -> bool | None:
    """Coerce to bool, preserving ``None`` as "unknown" rather than false."""
    return None if value is None else bool(value)


__all__ = [
    "Detail",
    "project_payload",
    "serialise_concept_view",
    "serialise_label_match",
    "serialise_nearest_match",
]
