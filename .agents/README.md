# groundworkers — OMOP concept mapping skills

This folder exposes three vocabulary mapping skills backed by the
**groundworkers** MCP service. Together they cover the core workflow of
mapping clinical terms and source codes to standard OMOP concepts — the
same atomic operations that underpin tools like groundcrew.

## Skills

| Skill | What it does |
|---|---|
| `map-clinical-term` | Map a free-text label or clinical term to ranked standard OMOP concepts |
| `map-source-code` | Map a source vocabulary code (ICD-10, SNOMED, RxNorm, …) to its standard OMOP equivalent |
| `explore-concept` | Retrieve hierarchy, relationships, and mapping context for a known OMOP concept_id |

## Typical workflow

1. You have a data dictionary row, a label, or a source code.
2. Use `map-clinical-term` or `map-source-code` to get candidate standard concepts.
3. Use `explore-concept` on the top candidate(s) to verify specificity and confirm the mapping makes clinical sense.

## Setup

Start the groundworkers MCP server pointing at your OMOP database:

```bash
cd groundworkers
uv run groundworkers --config config/groundworkers.local.yaml
```

See `config/groundworkers.example.yaml` for the full configuration reference.
