# Drug Concept Granularity

## RxNorm concept hierarchy

RxNorm organises drug concepts into levels of specificity. Standard mapping targets
exist at multiple levels, and the appropriate level depends on what information the
source provides.

| RxNorm concept class | Contains | Example |
|---------------------|----------|---------|
| Ingredient | Active substance only | Metformin |
| Clinical Drug Component | Ingredient + strength | Metformin 500 MG |
| Clinical Drug Form | Ingredient + dose form | Metformin Oral Tablet |
| Clinical Drug | Ingredient + strength + dose form | Metformin 500 MG Oral Tablet |
| Branded Drug | Clinical Drug + brand name | Glucophage 500 MG Oral Tablet |
| Marketed Product | Branded + packaging | Glucophage 500 MG Oral Tablet [30 Tablet] |

## Selection rule

**Use the most specific level the source data supports, up to Clinical Drug.**

- Source provides only drug name (no dose, no form) → map to **Ingredient**
- Source provides drug name + dose form → map to **Clinical Drug Form**
- Source provides drug name + strength + dose form → map to **Clinical Drug**
- Source provides dose but not form (or vice versa) → map to **Clinical Drug Component** or **Clinical Drug Form** as appropriate; prefer Ingredient if precision would be misleading

**Branded Drug and Marketed Product are not valid standard mapping targets.** Even
when the source specifies a brand name, map to the equivalent Clinical Drug or
Ingredient (which carries standard_concept = 'S'). Brand names are preserved in
`drug_source_value`.

## Why this matters

Mapping all drug concepts to Ingredient level maximises analytical cohesion: queries
for "Metformin" will find records regardless of formulation. Mapping too high (always
Ingredient) when formulation is known loses clinically meaningful precision. Mapping
to Branded level fragments the same clinical drug across multiple non-standard
concepts and breaks standard vocabulary queries.

## Dose form and strength information in source data dictionaries

Source data dictionaries often carry drug labels that include partial formulation
information ("Metformin HCl 500mg tabs"). Extract what is explicitly present in the
label; do not infer formulation from context. If the dose/form in the label is
ambiguous or inconsistent with known products, fall back to Ingredient.

## RxNorm Extension

When no RxNorm Ingredient or Clinical Drug exists for a drug (typically non-US
drugs or novel agents), RxNorm Extension provides equivalent concepts with the
same class hierarchy. Apply the same granularity rules. Prefer core RxNorm over
RxNorm Extension when both cover the same entity.
