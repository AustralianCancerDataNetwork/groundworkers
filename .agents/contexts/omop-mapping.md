# OMOP concept mapping — background context

## What OMOP is

The OMOP Common Data Model (CDM) is a standardised schema for observational
health data used by the OHDSI network. Clinical data from different source
systems (hospital EHRs, registries, claims databases) is transformed into
OMOP so analyses can run across multiple sites without bespoke code.

## Standard vs. non-standard concepts

Every concept in the OMOP vocabulary has a `standard_concept` flag:

- `S` (Standard) — the preferred concept for use in CDM tables. Analyses
  and cohort definitions use these concept_ids.
- `C` (Classification) — used for grouping/hierarchy but not in CDM tables.
- `null` (Non-standard) — a source concept. Exists in the vocabulary for
  mapping purposes but should not appear in CDM fact tables.

When mapping source data, the goal is always to find the `S` (Standard)
concept_id.

## Vocabularies

OMOP integrates many clinical vocabularies. Common ones:

| Vocabulary | Domain | Examples |
|---|---|---|
| SNOMED | Condition, Procedure, Observation | 44054006 = Type 2 diabetes |
| RxNorm | Drug | 1049502 = Metformin 500mg |
| LOINC | Measurement | 2339-0 = Glucose [Mass/volume] |
| ICD10CM | Condition (source) | E11.9 = Type 2 diabetes w/o complications |
| CPT4 | Procedure | 99213 = Office visit |

ICD, HCPCS, and ATC codes are typically non-standard source vocabularies
that map to SNOMED or RxNorm standard concepts.

## Domains

Domains indicate what CDM table a concept belongs to:

- `Condition` → `condition_occurrence`
- `Drug` → `drug_exposure`
- `Measurement` → `measurement`
- `Procedure` → `procedure_occurrence`
- `Observation` → `observation`
- `Device` → `device_exposure`

When grounding a term, supplying the expected domain reduces noise and
improves match precision.

## Mapping relationships

OMOP concept relationships encode clinical meaning:

- `Maps to` — the primary mapping edge from non-standard to standard
- `Is a` / `Subsumes` — parent/child hierarchy (e.g. SNOMED is-a tree)
- `Has ingredient` — drug → ingredient
- `Has finding site` — condition → anatomy

The groundworkers tools traverse these relationships automatically. You
rarely need to follow them manually unless you are building a custom
concept set or validating a mapping's specificity.
