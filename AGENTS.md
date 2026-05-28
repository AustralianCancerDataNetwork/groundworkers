# AGENTS.md

## Project overview

`groundworkers` is a read-only OMOP vocabulary integration package. It exposes
concept lookup, hierarchy navigation, free-text grounding, and embedding-based
search as both an MCP server and an importable Python library.

No patient-level data. No writes. No session state.

## Repo layout

```
src/groundworkers/
  app.py          — GroundworkersApp dataclass; build_adapters / build_application
  config.py       — Pydantic AppConfig and sub-configs
  server.py       — MCP server entry point (groundworkers CLI)
  adapters/       — OmopGraphAdapter, OmopVocabAdapter, OmopEmbAdapter
  services/       — MappingService (higher-level workflows over adapters)
  tools/          — MCP tool registrations (concept, resolver, search, mapping, embedding, system)
  base/           — shared server wrapper, error types, SQL helpers
tests/
  unit/           — adapter mocks; no live DB required
  integration/    — requires a live OMOP database (marked `integration`)
.agents/          — .agents standard skills, schemas, and MCP reference for this package
config/           — groundworkers.example.yaml
```

## Setup

```bash
uv sync --extra dev
uv run groundworkers --config config/groundworkers.example.yaml --describe
uv run groundworkers --config config/groundworkers.example.yaml
```

Copy `config/groundworkers.example.yaml` to `config/groundworkers.local.yaml`
and fill in your database URL before running locally.

## Testing

```bash
# unit tests only (no DB)
pytest -m "not integration"

# all tests including integration (requires live OMOP DB)
pytest
```

## Tool groups

| Group | Module | Key tools |
|---|---|---|
| Concept | `tools/concept_tools.py` | `concept_get`, `concept_by_code`, `concept_ancestors`, `concept_descendants`, `concept_relationships`, `concept_neighbors`, `concept_equivalency_path`, `concept_path`, `concept_map_to_standard` |
| Resolver | `tools/resolver_tools.py` | `concept_ground` |
| Search | `tools/search_tools.py` | `concept_search_exact`, `concept_search_fulltext`, `concept_navigate_to_standard` |
| Mapping | `tools/mapping_tools.py` | `concept_candidate_bundle`, `concept_search_normalized`, `concept_parent_backoff`, `concept_mapping_context`, `concept_map_to_value`, `concept_resolve_mapping_expression`, `mapping_evaluate_candidates` |
| Embedding | `tools/embedding_tools.py` | `embedding_search`, `embedding_neighbours`, `embedding_index_status`, `embedding_encode` |
| System | `tools/system_tools.py` | `system_status`, `system_vocabulary_catalogue` |

## Architecture notes

- Adapters own dependency-specific logic (SQLAlchemy queries, embedding index calls).
- `MappingService` composes adapters into higher-level workflows; tools call it rather
  than adapters directly where possible.
- `build_application(config)` is the canonical entry point for library use.
- Tool registration is split by group; `server.py` calls each `register_*` function.
- Integration tests are gated behind the `integration` pytest marker; unit tests mock
  adapters and require no external services.

## Code style

- Python 3.12+, Pydantic v2, SQLAlchemy 2.
- Ruff for linting; mypy for type checking.
- All tool handlers return `dict[str, Any]` with an `error` key on failure —
  never raise from a tool handler.
