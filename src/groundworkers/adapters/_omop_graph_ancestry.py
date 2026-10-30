"""Temporary omop-graph ancestry query primitive.

Upstream target: omop_graph.graph.queries.q_ancestor_rows. Keep this module free
of Groundworkers policy and runtime imports. Migration is documented in
docs/adapters/omop_graph.md; callers depend on OmopGraphAdapter.ancestor_rows.
"""

from collections.abc import Sequence
from typing import TypedDict

from omop_alchemy.cdm.model.vocabulary import Concept_Ancestor
from sqlalchemy import Select, select


class AncestorRow(TypedDict):
    descendant_concept_id: int
    ancestor_concept_id: int
    min_levels_of_separation: int
    max_levels_of_separation: int


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
