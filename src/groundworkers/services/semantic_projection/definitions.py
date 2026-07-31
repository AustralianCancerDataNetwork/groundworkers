"""Groundworkers' catalogue of deterministic output definitions.

`omop_semantics` supplies the generic building blocks (`OutputDefinition`,
`DerivationRule`, `SpecialValuePolicy`, CDM profiles); the concrete definitions
below are groundworkers-specific content, the same way a downstream
application authors its own CDM profile catalogue on top of a generic schema
library. They correspond to the `diagnosis-role-modifier` and
`criteria-gate-yesno` core knowledge packs — this is the deterministic
counterpart to that guidance, not a replacement for it.

Several definitions below apply to the Condition domain, so domain-only
matching cannot disambiguate between them — a caller must pass
`definition_hint` whenever multiple Condition-pattern definitions could fit.
Domain matching still resolves unambiguously once a single definition exists
for a non-colliding domain.
"""

from __future__ import annotations

from dataclasses import dataclass

from omop_semantics.runtime import (
    ContextFieldRef,
    DerivationRule,
    OutputDefinition,
    OutputLinkRule,
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


_FH_NON_EMITTING_CODES = frozenset({"00", "05", "06", "12", "66", "88", "99"})
_FH_DIAGNOSIS_CODE_MAP = {
    "01": 378419,   # Alzheimer's disease
    "02": 4196433,  # Senile dementia of the Lewy body type
    "03": 443605,   # Vascular dementia
    "04": 381316,   # Cerebrovascular accident
    "07": 374631,   # Motor neuron disease
    "08": 381270,   # Parkinson's disease
    "09": 444407,   # Prion disease
    "10": 432586,   # Mental disorder
    "11": 4182210,  # Dementia
}


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


FAMILY_HISTORY_CONDITION = OutputDefinition(
    name="family_history_condition",
    role="history_modifier",
    row_projections=(
        OutputRowProjection(
            row_id="family_history",
            profile_name="observation_coded",
            field_bindings={
                "observation_concept_id": 4167217,  # Family history of clinical finding
                "value_as_concept_id": ContextFieldRef("grounded.concept_id"),
            },
            special_value_policy=SpecialValuePolicy(
                source_field=ContextFieldRef("source.raw_value"),
                allowed_special_values=_FH_NON_EMITTING_CODES,
                suppression_mode="drop",
            ),
        ),
    ),
    notes=(
        "Family-history value-carried shape: the fixed entity concept "
        "4167217 ('Family history of clinical finding') carries the "
        "family-history meaning, while the grounded plain condition lands in "
        "value_as_concept_id. Caller may populate context.raw_value with the "
        "source diagnosis code; known non-emitting family-history codes "
        "(00, 05, 06, 12, 66, 88, 99) suppress the row.",
    ),
)


FAMILY_MEMBER_HISTORY_BUNDLE = OutputDefinition(
    name="family_member_history_bundle",
    role="relative_history_bundle",
    row_projections=(
        OutputRowProjection(
            row_id="relative_identity",
            profile_name="observation_string",
            field_bindings={
                "observation_concept_id": 0,
                "value_as_string": ContextFieldRef("source.raw_source_fields.relationship_label"),
            },
        ),
        OutputRowProjection(
            row_id="birth_year",
            profile_name="observation_numeric",
            field_bindings={
                "observation_concept_id": 3051549,
                "value_as_number": ContextFieldRef("source.raw_source_fields.birth_year"),
            },
        ),
        OutputRowProjection(
            row_id="age_at_death",
            profile_name="observation_numeric",
            field_bindings={
                "observation_concept_id": 3051544,
                "value_as_number": ContextFieldRef("source.raw_source_fields.age_at_death"),
            },
        ),
        OutputRowProjection(
            row_id="age_at_onset",
            profile_name="observation_numeric",
            field_bindings={
                "observation_concept_id": 3039465,
                "value_as_number": ContextFieldRef("source.raw_source_fields.age_at_onset"),
            },
        ),
        OutputRowProjection(
            row_id="method",
            profile_name="observation_string",
            field_bindings={
                "observation_concept_id": 0,
                "value_as_string": ContextFieldRef("source.raw_source_fields.method_label"),
            },
        ),
        OutputRowProjection(
            row_id="primary_diagnosis",
            profile_name="observation_coded",
            field_bindings={
                "observation_concept_id": 4167217,
                "value_as_concept_id": ContextFieldRef("grounded.concept_id"),
            },
            special_value_policy=SpecialValuePolicy(
                source_field=ContextFieldRef("source.raw_source_fields.primary_dx_code"),
                allowed_special_values=_FH_NON_EMITTING_CODES,
                suppression_mode="drop",
            ),
        ),
        OutputRowProjection(
            row_id="secondary_diagnosis",
            profile_name="observation_coded",
            field_bindings={
                "observation_concept_id": 4167217,
            },
        ),
    ),
    derivation_rules=(
        DerivationRule(
            target_row="secondary_diagnosis",
            target_slot="value_as_concept_id",
            source_field=ContextFieldRef("source.raw_source_fields.secondary_dx_code"),
            code_map=_FH_DIAGNOSIS_CODE_MAP,
            suppress_codes=_FH_NON_EMITTING_CODES,
        ),
    ),
    link_rules=(
        OutputLinkRule("relative_identity", "birth_year", "same_relative"),
        OutputLinkRule("relative_identity", "age_at_death", "same_relative"),
        OutputLinkRule("relative_identity", "age_at_onset", "same_relative"),
        OutputLinkRule("relative_identity", "method", "same_relative"),
        OutputLinkRule("relative_identity", "primary_diagnosis", "same_relative"),
        OutputLinkRule("relative_identity", "secondary_diagnosis", "same_relative"),
    ),
    notes=(
        "Family-member bundle for one relative. This illustrates why semantic "
        "projection is useful beyond one-row cases: one source relative can "
        "emit multiple coordinated OMOP rows. The relationship and method "
        "surfaces are kept as observation strings here because the current "
        "demo does not assert a standard OMOP concept for them. Primary "
        "diagnosis uses the grounded plain condition as a value-carried family-"
        "history observation; secondary diagnosis resolves from "
        "context.raw_source_fields.secondary_dx_code through the family-"
        "history diagnosis code map.",
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


YES_NO_OBSERVATION = OutputDefinition(
    name="yes_no_observation",
    role="coded_observation",
    row_projections=(
        OutputRowProjection(
            row_id="observation",
            profile_name="observation_coded",
            field_bindings={
                "observation_concept_id": ContextFieldRef("grounded.concept_id"),
            },
        ),
    ),
    derivation_rules=(
        DerivationRule(
            target_row="observation",
            target_slot="value_as_concept_id",
            source_field=ContextFieldRef("source.raw_value"),
            code_map={
                "1": 45877994,  # Yes
                "0": 45878245,  # No
            },
        ),
    ),
    notes=(
        "Ordinary Yes/No observation where both directions are informative. "
        "Unlike a criteria gate, '0' (No) still produces a row: caller must "
        "populate context.raw_value with '1' or '0', which maps into the OMOP "
        "value_as_concept_id slot.",
    ),
)


MEASUREMENT_NUMERIC_WITH_UNIT_FROM_CONTEXT = OutputDefinition(
    name="measurement_numeric_with_unit_from_context",
    role="quantitative_measurement",
    row_projections=(
        OutputRowProjection(
            row_id="measurement",
            profile_name="measurement_numeric_with_unit",
            field_bindings={
                "measurement_concept_id": ContextFieldRef("grounded.concept_id"),
                "value_as_number": ContextFieldRef("source.numeric_value"),
            },
        ),
    ),
    derivation_rules=(
        DerivationRule(
            target_row="measurement",
            target_slot="unit_concept_id",
            source_field=ContextFieldRef("source.raw_source_fields.unit_code"),
            code_map={
                "cm": 8582,
                "kg": 9529,
                "mm[Hg]": 8876,
            },
        ),
    ),
    notes=(
        "Quantitative measurement shape: caller populates context.numeric_value "
        "with the numeric reading and context.raw_source_fields.unit_code with "
        "a unit token ('cm', 'kg', or 'mm[Hg]'). This shows how a grounded "
        "measurement concept can combine a literal numeric value with a "
        "derived OMOP unit concept.",
    ),
)


BUILTIN_DEFINITIONS: tuple[tuple[OutputDefinition, DefinitionTrigger], ...] = (
    (CONDITION_WITH_STATUS_FROM_SECONDARY_FIELD, DefinitionTrigger(domains=frozenset({"Condition"}))),
    (FAMILY_HISTORY_CONDITION, DefinitionTrigger(domains=frozenset({"Condition"}))),
    (FAMILY_MEMBER_HISTORY_BUNDLE, DefinitionTrigger(domains=frozenset({"Condition"}))),
    (CRITERIA_GATE_CONDITION, DefinitionTrigger(domains=frozenset({"Condition"}))),
    (YES_NO_OBSERVATION, DefinitionTrigger(domains=frozenset({"Observation"}))),
    (
        MEASUREMENT_NUMERIC_WITH_UNIT_FROM_CONTEXT,
        DefinitionTrigger(domains=frozenset({"Measurement"})),
    ),
)
