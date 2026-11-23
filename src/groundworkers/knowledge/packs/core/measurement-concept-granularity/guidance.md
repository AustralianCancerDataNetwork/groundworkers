# Measurement: choosing the concept's level of detail

Use this when mapping a lab test or other measurement to `measurement_concept_id`. This pack's rules don't cover cancer staging, tumour attributes or genomic variants. Map those by their own conventions; Cancer Modifier and OMOP Genomic concepts remain valid targets.

## The rule

Choose the standard concept that keeps every part of the test the source gives, and adds none.

- **Never add a fact the source does not give.** Not a specimen, a method, a narrower sample (arterial, capillary), a timing or a challenge. This holds even when one specimen or method is the usual one for the test. A unit tells you the property, never the specimen.
- **Property is the exception.** If the source gives no unit, a test's property does not count as an added fact: the source may simply not carry the unit. Choose on the other parts. When the candidates that keep every other stated part differ only in property, choose one and map it; do not return needs_review for that reason alone.
- **"Blood" covers its fractions.** For a source that says "Blood", Serum, Plasma and Serum or Plasma do not add a specimen.
- **Never drop a stated part to avoid a property.** A specimen the source gives must be kept.
- **Leaving a part out is a loss; adding or contradicting a part is an error.** An error is worse than a loss. When no concept keeps every stated part, choose a broader one.
- **No vocabulary wins by default.** Choose by the parts, not by vocabulary.

## The parts of a test

| Part | What it is | LOINC part class |
|---|---|---|
| Component | The analyte | LOINC Component |
| Specimen | The sample | LOINC System |
| Property | How it is quantified; the unit implies it: mmol/L substance concentration, mg/dL or g/L mass concentration, % fraction, count per volume number concentration | LOINC Property |
| Time | Point in time, or a timed collection | LOINC Time |
| Scale, method | Quantitative or ordinal; how it is done | LOINC Scale, LOINC Method |

**Where to see them:**
- **LOINC:** `concept_mapping_context` lists a test's parts among its neighbours, by the classes above. `concept_relationships` shows the same links by name (`Has system`, `Has property` and so on).
- **SNOMED:** parts are less regular. Use `concept_relationships` (`Has component`, `Has specimen`, `Has direct site`, `Inheres in`, `Has property`), or the name.
- A part shown in neither the relationships nor the name is not stated.

## Specimen

- A different body fluid is a different test. Never swap a blood test for a body-fluid test, or the reverse.
- **"Blood" in a lab dictionary names the sample collected.** LOINC "Blood" (whole blood), "Serum", "Plasma" and "Serum or Plasma" all keep it. Choose among them by what the test is run on; the units, test name and lab section are evidence.
- **No specimen given** (and none in the label or lab section): choose a concept that states no specimen. That is a LOINC test with System "XXX", or a concept with no specimen. Do not choose a specimen-specific test, even the usual one.

## How to find it

1. **Search with the full source label,** including specimen and unit words. Search ranks short names first, so a query of the analyte alone returns broad concepts.
2. **Compare the parts of every candidate,** not the names. Tests that differ in specimen or property can share a name.
3. **Too broad** (drops a stated part): check its standard Measurement descendants for one that keeps it. The hierarchy is incomplete, so also search directly.
4. **Too specific** (adds a part), **or nothing keeps every stated part:**
   - **Sideways:** for a LOINC test, take its LOINC Component neighbour and call `concept_relationships` on it. Its `Component of` links list every LOINC test for that analyte. Choose the one that drops only the unsupported part, such as System "Body fluid" for a fluid LOINC lacks, System "XXX", or the version without the method or timing.
   - **Up:** standard ancestors are usually SNOMED measurements, from "X measurement, urine" to "X measurement". Take the nearest one that keeps every stated part, including the specimen, and contradicts nothing.
   - **Not targets:** LOINC Class, Hierarchy and Group concepts (not standard), and panels listed as ancestors (a group of tests, not a broader test).

## Vocabularies

- **LOINC and SNOMED:** most tests.
- **OMOP Extension:** tests OHDSI added where LOINC and SNOMED lack them.
- **CPT4 and HCPCS:** mostly billing codes that combine tests or name alternatives ("X or Y"). Such a code is broader than a source that names one test.

## When nothing is exact

Stop at the first that applies:
1. **Exact:** keeps every stated part, adds none.
2. **Broader:** keeps the most stated parts, adds and contradicts none. Say in the rationale what it does not keep.
3. **No valid mapping** (concept 0): nothing fits without adding or contradicting a part. Never force a near match.

## Sources

- OMOP CDM v5.4, MEASUREMENT conventions: https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement
- Book of OHDSI, 2nd edition, chapter 5 (sections 5.3.12, 5.3.13, Table 5.2): https://ohdsi.github.io/BookOfOhdsi-2ndEdition/representation/vocabularies.html
- LOINC Users' Guide, section 2.5 System/sample type: https://loinc.org/kb/users-guide/major-parts-of-a-loinc-term. LOINC is copyright © Regenstrief Institute, Inc., and is available at no cost under the license at https://loinc.org/license/.
