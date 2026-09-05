# Testing

## Running tests

```bash
# all tests (unit + integration; integration tests are skipped without a live DB)
pytest

# unit tests only (no database required)
pytest tests/unit/

# single file
pytest tests/unit/test_concept_tools.py
```

## Test configuration

Integration tests that query the OMOP database require a `.env` file at the repo root (or an environment-variable equivalent):

```dotenv
OMOP_CDM_DB_URL=postgresql://user:pass@localhost:5432/omop
```

Unit tests mock the adapter layer and run without a live database.

## What is tested

| Area | Scope |
|---|---|
| Concept tools | Deterministic concept lookup, hierarchy, relationship, path, neighborhood, classified-edge, and standard-mapping tools: correct responses, `NOT_FOUND` errors, `INVALID_INPUT` guards, and depth clamping |
| Resolver tools | `concept_ground`: empty query rejection, result ordering by score, match_kind on exact hits |
| Embedding tools | `embedding_index_status`, `embedding_neighbours`, `embedding_search`, `embedding_search_batch`, `embedding_encode`: correct responses, clamped limits, aligned batch rows, error propagation |
| Server registry | `create_server` with and without each adapter; system tools always present; adapter-conditional tool registration |
| Config validation | `OmopGraphConfig` schema name validation; `OmopEmbConfig` enabled-without-url guard; extra field rejection |
