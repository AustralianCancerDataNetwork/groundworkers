# Testing

## Test lanes

Groundworkers uses distinct lanes because an isolated unit run cannot prove the database, optional parser, plugin-entry-point, or built-wheel contracts.

| Lane | What it proves | CI behavior |
|---|---|---|
| Unit and service | Deterministic policy, adapter boundaries, source planning, transport behavior, and controlled provider responses | Runs without a live database or model service |
| Optional dependency | Real XLSX, DOCX, and PDF parsing plus missing-extra guidance | Required format tests must pass in the all-extras CI lane; missing-extra behavior is simulated |
| PostgreSQL integration | Seeded graph retrieval, real pgvector storage and reader behavior, model selection, filters, and embedding-tier grounding | Required: every required_integration case must be collected and pass |
| Wheel smoke | Base and optional-extra installations, package entry-point discovery, CLI startup, and successful MCP/REST planning operations | Builds once and runs outside the checkout |
| Optional live provider | Real configured model-provider connectivity | Opt-in developer smoke only; never substitutes for seeded embedding tests |

## Running tests

~~~bash
# Hermetic suite, including real optional-format parsers
uv run pytest -q -ra

# Unit tests only
uv run pytest tests/unit/

# Required PostgreSQL cases; provide a seeded test stack first
GROUNDWORKERS_CONFIG_PATH=/path/to/stack.toml \
GROUNDWORKERS_REQUIRE_INTEGRATION=1 \
GROUNDWORKERS_TEST_DATABASE_URL=postgresql+psycopg://... \
uv run pytest -m required_integration -q -ra

# Coverage for local review
COVERAGE_FILE=/tmp/groundworkers.coverage uv run pytest -q -ra \
  --cov=groundworkers --cov-branch --cov-report=term-missing
~~~

The required integration gate derives its cases from the `required_integration` marker, so every marked case is checked for collection and a passing result. Missing configuration, unavailable adapters, and absent seeded rows fail in that lane. The all-extras CI job applies the same execution check to `required_format` cases, so a missing parser cannot silently turn the format contract into a skip. Without the corresponding `GROUNDWORKERS_REQUIRE_*` flag, PostgreSQL and optional-format fixtures remain opt-in and skip with a reason.

## Configuration and optional provider smoke

Set GROUNDWORKERS_CONFIG_PATH (or GROUNDWORKERS_CONFIG) to the explicit Groundworkers stack file for integration tests. OA_CONFIG_PATH selects the oa-configurator default stack, but does not by itself opt the graph integration tests into a required run. PostgreSQL CI sets both paths and seeds a compact fixture before pytest starts.

The external-provider smoke tests are separate from seeded deterministic embedding tests. To run them locally, set GROUNDWORKERS_RUN_EMBEDDING_INTEGRATION=1, GROUNDWORKERS_CONFIG_PATH, and GROUNDWORKERS_TEST_CONCEPT_ID; they require a configured provider and a concept with stored vectors.

## Coverage and skip evidence

CI publishes coverage.xml and pytest.xml from the hermetic all-extras run. The JUnit report carries named skip reasons. PostgreSQL CI separately enforces the required test list, so optional local skips cannot satisfy that gate. Coverage percentages are review evidence, not a global pass threshold. Prioritize unexecuted behavior at database, provider, transport, and packaging boundaries.
