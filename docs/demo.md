# Demo

This is a real Claude Code session with the Groundworkers MCP server plugged into a local OMOP vocabulary.

## Basic lookups

In this video you see groundworkers load with no system memory or project settings - only groundworkers tools loaded. 

Simple lookup questions:

1. _Find the standard OMOP concept for ‘heart attack’_
2. _Which drugs make up the FOLFOX regimen, as OMOP standard RxNorm ingredients?_

<div class="asciicast" data-src="../static/demo.short.cast" data-start-at="40.9" data-poster-at="58.9"></div>

*Sped up: 39 s real time, 18 s playbacks.*

| Concept | concept_id | Vocabulary | Athena |
|---|---|---|---|
| Myocardial infarction ("heart attack") | 4329847 | SNOMED | [Open](https://athena.ohdsi.org/search-terms/terms/4329847) |
| FOLFOX (regimen) | 35806596 | HemOnc | [Open](https://athena.ohdsi.org/search-terms/terms/35806596) |
| fluorouracil | 955632 | RxNorm Ingredient | [Open](https://athena.ohdsi.org/search-terms/terms/955632) |
| leucovorin | 1388796 | RxNorm Ingredient | [Open](https://athena.ohdsi.org/search-terms/terms/1388796) |
| oxaliplatin | 1318011 | RxNorm Ingredient | [Open](https://athena.ohdsi.org/search-terms/terms/1318011) |

## Colorectal cancer concept set

Complex vocabulary inference:

1. **Initial concept set request:** _I would like a concept set covering colorectal cancer. This concept set should be split into groups covering each of the major relevant anatomical regions. Exclude non-malignant disease and non-solid tumors_
2. **Use topography clues to ask groundworkers to check completeness of the set:** _Let's audit the concept set specific to the ascending colon to check for completeness_
3. **Add in morphology-based verification as well:** _Could we use the snomed morphology property to map backwards to ICDO3 and provide additional confirmation that way as well?_


<div class="asciicast" data-src="../static/demo.short.cast" data-start-at="59.1"></div>

*Sped up: 6 min 10 s real time, 2 min 1 s playback. Only the waiting is compressed (spinner and counter updates while the model and tool calls run).*

| Concept | concept_id | Why look at it | Athena |
|---|---|---|---|
| Malignant tumor of large intestine | 443396 | The parent the whole concept set is built from (2,329 descendants) | [Open](https://athena.ohdsi.org/search-terms/terms/443396) |
| Neoplasm of ascending colon | 4130997 | The audit parent: everything under it is diffed against the ascending colon group | [Open](https://athena.ohdsi.org/search-terms/terms/4130997) |
| Large cell neuroendocrine carcinoma of ascending colon | 44502762 | ICD-O-3 outlier: standard, but not a descendant of 443396 because not all ICD-O-3 concepts attach to the SNOMED hierarchy | [Open](https://athena.ohdsi.org/search-terms/terms/44502762) |
| Primary malignant neuroendocrine neoplasm of ascending colon | 761001 | SNOMED outlier: sits under the ascending colon parent but not under 443396, so the hierarchy alone misses it | [Open](https://athena.ohdsi.org/search-terms/terms/761001) |
| Well-differentiated neuroendocrine tumor of ascending colon | 1077457 | Judgement call: it is the "Maps to" target of benign-carcinoid source codes, so it mixes behaviours | [Open](https://athena.ohdsi.org/search-terms/terms/1077457) |

## Data dictionary mapping

A toy data dictionary decomposition showing handling of real mapping problems beyond label:concept translation.

<div class="asciicast" data-src="../static/demo.short.cast" data-start-at="180.3"></div>

*Sped up: 1 min 27 s real time, 16 s playback*

| Concept | concept_id | Why look at it | Athena |
|---|---|---|---|
| Family history of clinical finding | 4167217 | The observation concept for `fhx_crc`; the condition goes in `value_as_concept_id` | [Open](https://athena.ohdsi.org/search-terms/terms/4167217) |
| Malignant colorectal neoplasm | 37168850 | The value used for `fhx_crc` | [Open](https://athena.ohdsi.org/search-terms/terms/37168850) |
| Malignant tumor of intestine | 443398 | The literal reading of "bowel cancer", which also includes the small intestine | [Open](https://athena.ohdsi.org/search-terms/terms/443398) |
| Current smoker | 40766945 | A LOINC question item used for `smoking`, with Yes/No answers as values | [Open](https://athena.ohdsi.org/search-terms/terms/40766945) |
| Smokes cigarette daily | 1450766 | The narrowest SNOMED alternative, since no plain "Current smoker" concept exists | [Open](https://athena.ohdsi.org/search-terms/terms/1450766) |
| Body mass index | 4245997 | The exact match for `bmi`; a LOINC concept was chosen instead, so pick one and be consistent | [Open](https://athena.ohdsi.org/search-terms/terms/4245997) |
| capecitabine | 1337620 | `chemo_agent_2`, mapped at Ingredient level (a FOLFOX/CAPOX component) | [Open](https://athena.ohdsi.org/search-terms/terms/1337620) |
| Colonoscopy | 4249893 | An exact match for `colonoscopy` (a Procedure) | [Open](https://athena.ohdsi.org/search-terms/terms/4249893) |

## Setup TUI

This part was recorded before the session above, and it's what you'd see on day one. Run
`groundworkers tui` and it walks you through connecting your CDM database, checks that each
backend is working (including how much of the vocabulary has embeddings), and lets you tweak
plugin settings such as UMLS.

<div class="asciicast" data-src="../static/demo.short.cast" data-start-at="0" data-poster-at="19"></div>

*Sped up: 1 min 22 s real time, 41 s in the recording. The setup waits are compressed.*
