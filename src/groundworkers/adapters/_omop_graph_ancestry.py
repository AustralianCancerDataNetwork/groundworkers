"""Temporary omop-graph ancestry query primitive.

Upstream target: omop_graph.graph.queries.q_ancestor_rows. Keep this module free
of Groundworkers policy and runtime imports. Migration is documented in
docs/adapters/omop_graph.md; callers depend on OmopGraphAdapter.ancestor_rows.
"""

from collections.abc import Sequence
from typing import TypedDict

from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Ancestor
from sqlalchemy import Select, func, select


class AncestorRow(TypedDict):
    descendant_concept_id: int
    ancestor_concept_id: int
    min_levels_of_separation: int
    max_levels_of_separation: int


class DescendantRow(TypedDict):
    descendant_concept_id: int
    min_levels_of_separation: int


def q_ancestor_rows(
    descendant_ids: Sequence[int],
    *,
    max_separation: int | None = None,
    include_self: bool = False,
) -> Select:
    """Select stored ancestry distances, including distinct zero-distance rows.

    Empty input selects no rows. Self means equal concept IDs, not distance zero.
    No concept eligibility, class, vocabulary or selection policy belongs here.
    """
    ids = tuple(dict.fromkeys(descendant_ids))
    if any(type(value) is not int or value <= 0 for value in ids):
        raise ValueError("descendant_ids must contain positive integers")
    if max_separation is not None and (
        type(max_separation) is not int or max_separation < 0
    ):
        raise ValueError("max_separation must be a nonnegative integer or None")
    statement = select(
        Concept_Ancestor.descendant_concept_id,
        Concept_Ancestor.ancestor_concept_id,
        Concept_Ancestor.min_levels_of_separation,
        Concept_Ancestor.max_levels_of_separation,
    ).where(Concept_Ancestor.descendant_concept_id.in_(ids))
    if max_separation is not None:
        statement = statement.where(
            Concept_Ancestor.min_levels_of_separation <= max_separation
        )
    if not include_self:
        statement = statement.where(
            Concept_Ancestor.ancestor_concept_id
            != Concept_Ancestor.descendant_concept_id
        )
    return statement.order_by(
        Concept_Ancestor.descendant_concept_id,
        Concept_Ancestor.min_levels_of_separation,
        Concept_Ancestor.ancestor_concept_id,
    )


def q_descendant_count(
    ancestor_id: int,
    max_separation: int,
    *,
    concept_class_ids: Sequence[str] | None = None,
) -> Select:
    """Count stored descendants within a minimum-separation bound."""
    if type(ancestor_id) is not int or ancestor_id <= 0:
        raise ValueError("ancestor_id must be a positive integer")
    if type(max_separation) is not int or not 1 <= max_separation <= 10:
        raise ValueError("max_separation must be between 1 and 10")
    statement = select(func.count()).select_from(Concept_Ancestor)
    if concept_class_ids is not None:
        if not 1 <= len(concept_class_ids) <= 20 or any(
            not isinstance(value, str) or not value for value in concept_class_ids
        ):
            raise ValueError("concept_class_ids must contain 1 to 20 non-empty strings")
        statement = statement.join(
            Concept, Concept.concept_id == Concept_Ancestor.descendant_concept_id
        ).where(Concept.concept_class_id.in_(concept_class_ids))
    return statement.where(
        Concept_Ancestor.ancestor_concept_id == ancestor_id,
        Concept_Ancestor.descendant_concept_id != ancestor_id,
        Concept_Ancestor.min_levels_of_separation <= max_separation,
    )


def q_descendant_rows(
    ancestor_id: int,
    max_separation: int,
    *,
    limit: int | None = None,
    offset: int = 0,
    concept_class_ids: Sequence[str] | None = None,
) -> Select:
    """Select stored descendants in deterministic order, optionally paged."""
    if type(ancestor_id) is not int or ancestor_id <= 0:
        raise ValueError("ancestor_id must be a positive integer")
    if type(max_separation) is not int or not 1 <= max_separation <= 10:
        raise ValueError("max_separation must be between 1 and 10")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 500):
        raise ValueError("limit must be between 1 and 500")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    statement = select(
            Concept_Ancestor.descendant_concept_id,
            Concept_Ancestor.min_levels_of_separation,
        ).select_from(Concept_Ancestor)
    if concept_class_ids is not None:
        if not 1 <= len(concept_class_ids) <= 20 or any(
            not isinstance(value, str) or not value for value in concept_class_ids
        ):
            raise ValueError("concept_class_ids must contain 1 to 20 non-empty strings")
        statement = statement.join(
            Concept, Concept.concept_id == Concept_Ancestor.descendant_concept_id
        ).where(Concept.concept_class_id.in_(concept_class_ids))
    statement = statement.where(
            Concept_Ancestor.ancestor_concept_id == ancestor_id,
            Concept_Ancestor.descendant_concept_id != ancestor_id,
            Concept_Ancestor.min_levels_of_separation <= max_separation,
        ).order_by(
            Concept_Ancestor.min_levels_of_separation,
            Concept_Ancestor.descendant_concept_id,
        )
    if limit is not None:
        statement = statement.limit(limit)
    if offset:
        statement = statement.offset(offset)
    return statement
