# Proposal: per-domain standard-resolution packs

Status: draft for review (2026-10-19). Replaces the single
`core/standard-concept-preference` pack with a slim, always-loaded core
plus one pack per OMOP domain.

## Why the broad pack falls short

`standard-concept-preference` mixes two kinds of content:

1. **Invariants** that hold in every domain: the target must be
   `standard_concept = 'S'`, active, never a classification (`'C'`), and
   `concept_id = 0` when nothing fits.
2. **Per-domain resolution rules**, compressed to one line each, e.g.
   "Procedure: SNOMED preferred, CPT4 for US billing".

The second kind is where agents actually go wrong, and one line per domain
cannot carry it. Checking the procedure domain against the live vocabulary
(v5.0 27-FEB-26) shows the one-liner is misleading:

- Standardness is decided **per code, not per vocabulary**. CPT4 has
  ~10.2k standard codes and ~3.8k non-standard ones that map to SNOMED,
  CVX or RxNorm. OPCS4 and ICD9Proc are split roughly half and half.
- A standard source code (CPT4, ICD10PCS, standard HCPCS) **maps to
  itself**. There is no "prefer SNOMED over CPT4" step: rewriting a
  standard code to SNOMED discards information and is wrong.
- The vocabulary ships cross-vocabulary links that *look* like equivalences
  but aren't (`CPT4 - SNOMED eq` often points to a broader concept, or to
  several). An agent told only "prefer SNOMED" will follow them.
- Concept sets built from SNOMED descendants miss standard CPT4/ICD10PCS
  records (e.g. CPT4 27447 total knee arthroplasty is not a descendant of
  SNOMED 43531648 Total knee replacement).

None of this applies to Condition, where one standard vocabulary (SNOMED)
absorbs everything through `Maps to`. Each domain resolves to
standardness differently, so each needs its own guidance.

## Proposed structure

```
packs/core/
  standard-concept-preference/      # KEEP name (always: true) — invariants only
  standard-resolution-condition/    # domains: [Condition]
  standard-resolution-procedure/    # domains: [Procedure]       ← drafted
  standard-resolution-measurement/  # domains: [Measurement]
  standard-resolution-observation/  # domains: [Observation]
  standard-resolution-drug/         # domains: [Drug]
  standard-resolution-device/       # domains: [Device]
  drug-concept-granularity/         # unchanged; see_also from -drug
```

The catalogue only discovers `packs/<layer>/<pack>/manifest.yaml`, so the
domain packs are flat siblings rather than nested children. The link
between them is `see_also` plus `applicability.domains`, which
`PackApplicability.matches()` already filters on.

### Slim core: `standard-concept-preference`

Keep the name. It is always loaded and the bundled-baseline test asserts it
exists (`tests/unit/test_knowledge_catalogue.py:218`). Contents:

- Active-target rule. Follow `Maps to` from `U`; never target `D`.
- `S` required for `*_concept_id`. `C` is never a target. Non-standard goes
  in `*_source_concept_id`.
- **New:** standardness is per code. Check `standard_concept` on the
  concept, never infer it from `vocabulary_id`.
- **New:** a standard source concept maps to itself. Do not re-map it to a
  "preferred" vocabulary.
- **New:** `Maps to` is the only mapping edge. `eq`, `cat`, `poss_eq` and
  `Is a` are evidence, not mappings.
- `concept_id = 0` fallback.
- Loading rule: "after resolving a target's `domain_id`, load
  `standard-resolution-<domain>`".
- The `vocab_preference` block is removed and moves into the domain packs.

### Domain pack template

Every `standard-resolution-<domain>` pack uses the same sections, so an
agent knows where to look:

| Section | Purpose |
|---|---|
| 1. Standard vocabularies in this domain | Which vocabularies hold `S` concepts here, and roughly what share. Snapshot counts carry a `verified_against` vocabulary version. |
| 2. Resolution path by input type | (a) code from a standard vocabulary, (b) non-standard code, (c) free text. Each has its own path. |
| 3. Relationship semantics | Which relationship_ids are mappings, which are evidence, and the known failure modes of each. |
| 4. Concept-set implications | Whether descendants of one vocabulary cover the others. If not, how to build a set that does. |
| 5. Traps | Concrete patterns the vocabulary gets wrong, with concept IDs. |
| 6. Verification checklist | What to check before accepting a target. |

`rules.yaml` carries the machine-readable half under the same keys:
`standard_vocabularies`, `resolution_paths`, `relationship_semantics`,
`concept_set_roots`, `traps`, `checks`. Worked cases go in
`examples.yaml`, which `PackContent` already loads.

### What each domain pack must cover (outline)

- **Condition:** SNOMED is the only standard vocabulary. ICD10CM/ICD9CM/ICD10
  reach it via `Maps to`, sometimes 1:n (see `one-to-many-mapping`). Covers
  `Maps to value` for pre-coordinated codes (see `pre-coordinated-split`),
  Cancer Modifier / ICDO3 for oncology, and the domain-change trap (an
  ICD10CM code mapping into Observation).
- **Procedure:** see the draft. Multiple standard vocabularies, self-maps,
  `eq`/`cat` semantics, attribute-based alignment, concept sets with
  several roots.
- **Measurement:** LOINC and SNOMED are both standard. LOINC wins for
  lab tests. Covers CPT4 lab codes (1.3k standard CPT4 Measurement codes,
  plus `CPT4 - LOINC eq`) and the test+result split.
- **Observation:** SNOMED / LOINC / CPT4 E&M / HCPCS. Covers history-of
  (see `history-of-event`) and E&M visit codes landing in Observation.
- **Drug:** RxNorm before RxNorm Extension. Covers HCPCS J-codes and CPT4
  vaccine/drug codes that are non-standard and map to RxNorm/CVX.
  Granularity is delegated to `drug-concept-granularity`.
- **Device:** HCPCS Level II is the main standard source (5.2k S). Covers
  SNOMED physical objects and the Procedure/Device split for
  implant-plus-procedure codes.

## Mechanism note

`post-filter` is declared in manifests, but nothing reads `rules.yaml`
programmatically today. `knowledge_pack` only returns it in the payload
(`tools/knowledge_tools.py:189`). The domain packs keep `rules.yaml`
declarative so a future post-filter can use it. The guidance is written to
stand on its own when it is context-injected.

## Migration

1. Land `standard-resolution-procedure`. It adds content and changes
   nothing existing.
2. Draft the remaining domain packs from the template.
3. Slim `standard-concept-preference` to the invariants and add the
   loading rule. Bump it to `version: "2.0"`.
4. Mirror into `gw-plugins/kb-prompts/packs/core/`, whose
   `standard-concept-preference` is currently byte-identical.
