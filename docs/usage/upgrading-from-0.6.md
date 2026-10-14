# Upgrading from Groundworkers 0.6

Groundworkers 0.7 uses the oa-configurator 2.0 stack and supports a vocabulary database separate from the primary CDM database.

## Configuration changes

- Rename the former database `schema_name` setting to `cdm_schema` for a CDM database. Use `vocab_schema` when vocabulary has a distinct schema. Generic databases, including vector-store databases, still use `schema_name`.
- SQLite has no schemas. Remove schema settings from SQLite database entries; setup ignores those fields and tells you when they are unavailable.
- A PostgreSQL vector store colocated with the CDM uses the dedicated `groundworkers` schema. Its database is a generic entry on the CDM connection:

  ```toml
  [databases.groundworkers_vectors]
  kind = "generic"
  connection = "cdm_main"
  schema_name = "groundworkers"
  ```

- Add databases with the 2.0 CLI syntax, for example:

  ```bash
  omop-config databases add cdm clinical --connection cdm_main --cdm-schema public
  omop-config databases add generic vectors --connection cdm_main --schema-name groundworkers
  ```

- Startup now fails fast if either the primary or vocabulary endpoint cannot be reached. The console prints the first scrubbed cause and a database recovery hint.
- A pre-3.0 embedding store reports `LEGACY_REGISTRY`. The migration script is tracked in oa-configurator issue #40; once it ships, Groundworkers will provide the omop-emb migration command.
- To split the vocabulary, set `vocab_connection` on the CDM database entry. Groundworkers 0.7 routes vocabulary reads to that connection, including setup diagnostics and graph searches.

## Plugin changes

See [Upgrading plugins to Groundworkers 0.7](../development/plugins.md#upgrading-plugins-to-groundworkers-07) for the full API checklist. Plugins should require `groundworkers>=0.7,<0.8`, pass `cdm_session_factory` for OMOP graph/vocabulary access, and treat `embedding_backend_factory()` as a read-only `EmbeddingStoreReader`.
