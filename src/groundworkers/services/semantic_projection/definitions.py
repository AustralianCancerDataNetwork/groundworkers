"""Groundworkers' catalogue of deterministic output definitions.

`omop_semantics` supplies the generic building blocks (`OutputDefinition`,
`DerivationRule`, `SpecialValuePolicy`, CDM profiles); the concrete definitions
below are groundworkers-specific content, the same way a downstream
application authors its own CDM profile catalogue on top of a generic schema
library. They correspond to the `diagnosis-role-modifier` and
`criteria-gate-yesno` core knowledge packs — this is the deterministic
counterpart to that guidance, not a replacement for it.

Both definitions below apply to the Condition domain, so domain-only matching
cannot disambiguate between them — a caller must pass `definition_hint`
whenever both could plausibly apply (i.e. always, today). Domain matching still
resolves unambiguously once a definition with a non-colliding domain is added.
"""

from __future__ import annotations

from dataclasses import dataclass

from omop_semantics.runtime import (
    ContextFieldRef,
    DerivationRule,
    OutputDefinition,
    OutputRowProjection,
    SpecialValuePolicy,
)


@dataclass(frozen=True)
class DefinitionTrigger:
    """Declares which grounded domains a definition applies to.

    Deliberately simple: domain-only matching. Concept-group-aware matching
    (e.g. "only for descendants of concept X") belongs in `omop_semantics`
    itself if it turns out to be needed by more than one consumer — see
    agent-stack's SEMANTIC_INTEGRATION design notes. Until then, an ambiguous
    domain match is reported as `no_match` rather than guessed.
    """

    domains: frozenset[str] = frozenset()


CONDITION_WITH_STATUS_FROM_SECONDARY_FIELD = OutputDefinition(
    name="condition_with_status_from_secondary_field",
    role="condition_modifier",
    row_projections=(
        OutputRowProjection(
            row_id="condition",
            profile_name="condition_with_status",
            field_bindings={
                "condition_concept_id": ContextFieldRef("grounded.concept_id"),
            },
        ),
    ),
    derivation_rules=(
        DerivationRule(
            target_row="condition",
            target_slot="condition_status_concept_id",
            source_field=ContextFieldRef("source.raw_source_fields.role_field"),
            code_map={
                "1": 32902,  # Primary diagnosis
                "2": 32908,  # Secondary diagnosis — used for "Contributing"; no dedicated concept exists
            },
            suppress_codes=frozenset({"3"}),  # Non-contributing — the diagnosis is not asserted at all
        ),
    ),
    notes=(
        "Diagnosis paired with a separately-collected Primary/Contributing/"
        "Non-contributing role field. Caller must populate "
        "context.raw_source_fields.role_field with the role field's raw code "
        "(1, 2, or 3), regardless of what that field is actually named in the "
        "source data.",
    ),
)


CRITERIA_GATE_CONDITION = OutputDefinition(
    name="criteria_gate_condition",
    role="condition_modifier",
    row_projections=(
        OutputRowProjection(
            row_id="condition",
            profile_name="condition_simple",
            field_bindings={
                "condition_concept_id": ContextFieldRef("grounded.concept_id"),
            },
            special_value_policy=SpecialValuePolicy(
                source_field=ContextFieldRef("source.raw_value"),
                allowed_special_values=frozenset({"0"}),
                suppression_mode="drop",
            ),
        ),
    ),
    notes=(
        "Yes/No field phrased 'meets criteria for X' (e.g. 'does the "
        "participant meet the criteria for dementia?'). Caller must populate "
        "context.raw_value with the field's own raw code; '0' (No) suppresses "
        "the row — a negative answer carries no positive clinical content of "
        "its own.",
    ),
)


BUILTIN_DEFINITIONS: tuple[tuple[OutputDefinition, DefinitionTrigger], ...] = (
    (CONDITION_WITH_STATUS_FROM_SECONDARY_FIELD, DefinitionTrigger(domains=frozenset({"Condition"}))),
    (CRITERIA_GATE_CONDITION, DefinitionTrigger(domains=frozenset({"Condition"}))),
)
