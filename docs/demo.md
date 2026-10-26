# Demo

A recorded session of Claude Code using the Groundworkers MCP server against a local OMOP
vocabulary. The recording is one terminal session split into four parts. Each player starts at
its part and pauses where the next one begins, and plays at 1.5× speed. Use the player controls
to pause, seek or change speed.

## Basic lookups

Claude Code starts with only the Groundworkers MCP server loaded and answers two vocabulary
questions:

- Find the standard OMOP concept for "heart attack".
- Which drugs make up the FOLFOX regimen, as OMOP standard RxNorm ingredients?

<div class="asciicast" data-src="../static/demo.cast" data-start-at="81.6" data-poster-at="120.5"></div>

## Colorectal cancer concept set

A longer, multi-step task:

- Build a colorectal cancer concept set, grouped by anatomical region, excluding non-malignant
  disease and non-solid tumours.
- Audit the ascending colon group for completeness.
- Cross-check it by mapping SNOMED morphology back to ICD-O-3.

<div class="asciicast" data-src="../static/demo.cast" data-start-at="120.8"></div>

## Data dictionary mapping

A short colorectal registry data dictionary is shown as a table, planned with `source_plan`, and
then each field is mapped to a standard OMOP concept, returned as one table.

<div class="asciicast" data-src="../static/demo.cast" data-start-at="491.1"></div>

## Setup TUI

Before the session above, the Groundworkers setup TUI (`groundworkers tui`) walks through the
CDM database connection, checks each configured backend, including embedding coverage, and shows
the plugin settings pages such as UMLS.

<div class="asciicast" data-src="../static/demo.cast" data-start-at="0" data-poster-at="38"></div>
