# groundworkers

**groundworkers** is an atomic, read-only MCP (Model Context Protocol) tool library for
navigating the OMOP vocabularies.  It exposes OMOP vocabulary lookups, embedding similarity search, 
cohort concept references, and system status as typed MCP tools that any MCP client can call —
including [groundcrew](https://github.com/AustralianCancerDataNetwork/groundcrew),
Claude Code, and autonomous agents.

Read-only.  No patient-level data.  No write operations.

## What it exposes

| Group | Tools |
|---|---|
| **Concept** | `concept_get`, `concept_by_code`, `concept_ancestors`, `concept_descendants`, `concept_relationships`, `concept_equivalency_path`, `concept_path`, `concept_map_to_standard`, `concept_neighbors` |
| **Resolver** | `concept_ground` (with `parent_ids`, scoring fields, and `grounding_explanation`) |
| **Search** | `concept_search_exact`, `concept_search_fulltext`, `concept_navigate_to_standard` |
| **Embedding** | `embedding_index_status`, `embedding_neighbours`, `embedding_search`, `embedding_encode` |
| **Cohort** | `cohort_find_concept_references` |
| **System** | `system_status`, `system_vocabulary_catalogue` |

Tools are registered conditionally — if an adapter is not configured, its tools are
simply not registered.  `system_status` and `system_vocabulary_catalogue` are always
registered so clients can always query adapter availability.

## Quick start

```bash
uv venv
uv sync --extra dev --extra embedding-tools
uv run groundworkers --config config/groundworkers.example.yaml --describe
```

Start the server:

```bash
uv run groundworkers --config config/groundworkers.example.yaml
```

## Example config

```yaml
omop_graph:
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  vocab_schema: omop_vocab

omop_emb:
  enabled: true
  backend_type: pgvector
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  default_model_name: qwen3-embedding:0.6b
  api_base: "http://localhost:11434/v1"
  api_key: "ollama"
```

## Install matrix

| Use case | Extras |
|---|---|
| Core server only | none |
| Concept tools | `concept-tools` |
| Cohort tools | `cohort-tools` |
| Embedding tools (sqlite-vec) | `embedding-tools` |
| Embedding tools (pgvector) | `embedding-pgvector` |
| Embedding tools (FAISS sidecar) | `embedding-faiss` |
| All tool families | `all-tools` |
| All + pgvector embeddings | `all-tools-pgvector` |
| All + FAISS embeddings | `all-tools-faiss` |
| Development | `dev` |
| Development + all tools | `dev-all` |

## Layout

```
src/groundworkers/
  adapters/          — omop_graph, omop_emb, oa_cohorts adapter classes
  base/              — GroundcrewServer, errors, results, SQL helpers
  tools/             — MCP tool registrations by domain
  config.py          — Pydantic config models (AppConfig, OmopGraphConfig, etc.)
  server.py          — Server factory and CLI entry point
config/              — Example YAML configs
_design/             — Architecture notes and spec documents
tests/               — Unit and integration tests
```

## Adapter backends

- **omop-graph** — concept lookup, hierarchy traversal, full-text search
- **omop-emb** — embedding index (sqlite-vec, pgvector, or FAISS sidecar)
- **OpenAnalytics cohorts** — cohort concept reference queries (Phase N)

## Companion repos

- [groundcrew](https://github.com/AustralianCancerDataNetwork/groundcrew) — ACP orchestration layer that drives this tool substrate
- [omop-graph](https://australiancancerdatanetwork.github.io/omop-graph/) — OMOP virtual knowledge graph library
- [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/) — OMOP embedding index library
