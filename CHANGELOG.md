## 1.0.0 (unreleased)

Stack 1.0 cutover: oa-configurator 1.x, omop-alchemy 1.x, omop-graph 2.x,
omop-emb 2.x, omop-llm 1.x. No compatibility shims are provided for the removed
0.x shapes.

!!! danger "Release blocker"
    `[tool.uv.sources]` pins `oa-configurator` to its unreleased `path_config`
    branch, which makes path-based config loading public. Delete that section and
    raise the version floor before publishing: no source or editable dependency
    may reach a published artifact. `tests/unit/test_migration_boundaries.py`
    pins the exact entry, so removing it fails that test until the assertion is
    restored to "no sources".

The `tui` extra resolves `groundskeeping>=0.5.1,<1` from PyPI, with no source pin.

### Removed — standalone surfaces

- `groundworkers --projection-tui` / the `projection-tui` command, and
  `services/semantic_projection/tui_launcher.py` with it. The explorer was
  omop-semantics' own `OutputDefinitionExplorer` driven second-hand through a
  Groundworkers subclass and a set of sample payloads; the whole module existed
  to serve it. Semantic projection remains available as an MCP tool, which
  builds its requests from typed parameters and never used this path.
- `scripts/demo.py`. It was referenced nowhere, hardcoded database credentials
  outside oa-configurator, and called six adapter methods and two constructor
  arguments that no longer exist.

### Fixed — logging, identifier quoting, enum coercion

- **Logging is now actually configured.** `GroundworkersConfig` has always
  declared `extra_logging_namespaces = ("omop_graph", "omop_emb")`, but nothing
  ever called `configure_logging`, so the declaration was inert, the stack's
  `[logging]` section was ignored, and oa-configurator's `RedactingFormatter`
  was never installed. `main()` now configures logging twice: once immediately
  after parsing arguments, so oa-configurator's own load-time warnings (a config
  file with loose permissions, for one) are formatted rather than falling
  through to Python's last-resort handler; and again with the stack once it is
  readable, so `[logging]` takes effect. A `-v` / `-vv` flag sets verbosity.
  Handlers write to **stderr**, so this never interferes with the stdio MCP
  transport.
- Path-based config loading comes from oa-configurator's public
  `load_stack_config_from_path`. Groundworkers' `bootstrap.py` had a
  near-line-for-line reimplementation of upstream's private `_load_from_path`,
  which had drifted: it never warned about a config file other users can read,
  despite that file holding database passwords, and it re-parsed on every call
  instead of using upstream's stat-keyed cache. Both are gained by the swap.
- Identifier quoting goes through SQLAlchemy's dialect preparer
  (`base/sql.py::quote_identifier`). `application/setup/databases.py` had its own
  copy that hardcoded ANSI double quotes regardless of dialect, while
  `embedding_population.py` already used the preparer.
- One `enum_value` in `base/results.py` replaces three near-copies. The variant
  in `embedding_setup.py` returned `str` rather than `str | None`, so a null
  column would have reached the operator as the literal text `"None"`; the two
  call sites reading `NOT NULL` registry columns now use `required_enum_value`,
  which raises instead.

### Changed — secret handling is centralised upstream

- Requires `oa-configurator>=1.2.1` and `groundskeeping>=0.5.1`, which centralise
  the secret primitives the stack previously duplicated.
- Endpoint redaction now comes from oa-configurator's `safe_endpoint`, imported
  directly at each use. Groundworkers previously carried **three** separate
  copies with three different secret word lists — `config.py`,
  `application/setup/embedding_setup.py`, and
  `application/setup/runtime_setup.py` — which is how the stack-wide drift
  started. No redaction code and no secret word list remains in this package.
- Two behaviour changes follow from upstream's rules, both intended: every
  query *value* is masked rather than only those whose parameter name matched a
  word list (so `?region=au` renders as `?region=***`), and the userinfo
  username is preserved while its password is masked, matching
  `safe_url`. `***` is also no longer percent-encoded as `%2A%2A%2A`.
- `describe()` reports `api_key_configured: bool` instead of a literal `"***"`.
  `ResolvedProvider` is a dataclass carrying no `Sensitive()` marker to consult,
  and in a JSON payload bound for an agent a fake mask reads like a value.
- The setup wizard's "a blank secret answer keeps the stored one" rule now
  derives its field set from the field specs' own `sensitive` flag rather than a
  second hardcoded list.
- The apply-review diff decides what to mask by walking the schema for
  `Sensitive()` markers instead of pattern-matching flattened config paths. That
  review is where a credential would actually surface to an operator, and it had
  no test; one now asserts the secret is absent from the rendered diff while the
  field still shows as changed.
- URL fragments are masked (`#***`) by oa-configurator 1.2.1, so a token cannot
  ride along in one. The interim local wrapper that existed only to drop the
  fragment under 1.2.0 has been deleted.

### Changed — `[tools.groundworkers]` is flat

- The six nested sub-tables (`mcp`, `rest`, `grounding`, `source_planning`,
  `knowledge`, `semantic_projection`) are replaced by flat fields grouped by
  name prefix: `mcp_transport`, `rest_base_path`, `grounding_max_depth`, and so
  on. This matches every other package in the stack.
- Fixes silent config loss. The shared configure path replaces a nested
  sub-table wholesale on any partial update, so setting one subfield reset its
  untouched siblings to defaults — `--set mcp.transport=sse` also reverted
  `mcp.host` and `mcp.port`. Flat fields update independently.
- Fixes six unusable CLI flags. `--mcp`, `--rest`, `--grounding`,
  `--source-planning`, `--knowledge` and `--semantic-projection` were typed as
  strings and rejected every value with "Input should be a valid dictionary".
  Between that and having no setup-console journey, those settings were
  reachable only by hand-editing TOML.
- Every field now carries `Field(description=...)`, which is the CLI flag help,
  the interactive prompt, and the generated docs. The reasoning was previously
  in `#` comments, invisible on all three.
- `AppConfig` keeps its role — one resolved runtime picture shared by the tool
  registry, both transports, `--describe`, and the setup console — but drops the
  unused `resolver` field and the pass-through properties that gave each setting
  a second name. Settings are read through `config.groundworkers.<field>`.

### Changed — chat model configuration

- The chat model is now a named `[models.*]` entry reached through
  `groundworkers.llm_model_name`, exactly like `embedding_model_name`. The
  `[tools.groundworkers.llm]` mapping (`enabled`, `provider`, `api_base`,
  `api_key`, `default_model_name`) is **removed**: its endpoint and credentials
  belong on the `[providers.*]` entry the model references, where
  oa-configurator already marks the key `Sensitive()` and redacts it.
- There is no separate `llm.enabled` flag. Chat is available exactly when
  `llm_model_name` resolves, matching how embeddings are already gated by
  `embedding_model_name` + `vector_store_name`.
- `LLMAdapter` runs on omop-llm's provider-neutral `ModelBackend` instead of
  constructing an OpenAI client. It keeps its `complete_text` /
  `complete_structured` / `status` contract and its `GroundworkersError` codes;
  what it no longer owns is a provider transport. `status()` now reports the
  model's declared `structured_output` capability rather than assuming `True`.
- Setup-time model inventory reads the provider's OpenAI-compatible `/models`
  endpoint over plain HTTP, matching the Ollama tag listing beside it. omop-llm
  can say *whether* a provider is reachable but discards the model list, so
  inventory still comes from the provider's own surface.
- The `llm` extra is **removed**. Nothing imports the OpenAI SDK any more;
  omop-llm is a core dependency.
- The chat setup journey writes a `[providers.*]` and a `[models.*]` entry
  through the same provider boundary as the embedding journey, and its review
  now names the real entries it will create.

### Fixed

- `config/groundworkers.example.toml` was still in 0.x shape (`[resources.*]`,
  `[resource_aliases]`, `[tools.*.extra]`, `[tools.omop_emb.extra]`) and could
  not be loaded at all under 1.0. Rewritten, and a test now parses it so it
  cannot rot again silently.
- `make setup` referenced a non-existent `embedding-tools` extra, and `make
  describe` a `config/groundworkers.example.yaml` that has never existed.

### Changed — build and CI/CD

- Adopted the shared cava-devops pipeline. `ci.yml` (label gate + build/test),
  `merge.yml` (release drafter), `publish.yml`, and `docs.yml` now call the
  reusable workflows; the hand-rolled `pypi.yml` is deleted and `docs.yml` no
  longer pins its own MkDocs install.
- Versioning moved to `hatch-vcs`: the build backend is `hatchling`, and the
  version is derived from the git tag rather than a static `version` field. A
  release is now cut by publishing a tag, and `pyproject.toml` no longer has to
  be kept in step with it.
- Releases are gated on a `breaking` / `feature` / `fix` / `dependencies` label,
  which also determines the version bump. `chore` bypasses the gate.
- `ruff` and `ty` run on every pull request, and both are clean. The ruff rule
  set is pinned explicitly so a ruff release cannot widen it and break CI
  without a code change.
- `.githooks/pre-commit` keeps `uv.lock` in step with `pyproject.toml`. Run
  `git config core.hooksPath .githooks` once per clone to enable it.

### Removed

- `[resources.*]` bundles, `resource_aliases`, `default_resource`, `[profiles.*]`,
  `active_profile`, and `[tools.*.extra]`. Use `[connections.*]`, `[databases.*]`,
  `[providers.*]`, `[models.*]`, and `[vector_stores.*]`, referenced by name.
- Profile selection: the `--profile` CLI option and `OA_ACTIVE_PROFILE`. Use
  separate config files with `--config-path` / `OA_CONFIG_PATH`.
- `has_tool_config()`, `resolve_cdm_resource_name()`,
  `resolve_embedding_resource_name()`.
- `application/setup/database_configuration.py` and its private database wizard;
  `application/setup/llm_configuration.py` and `tui/wizards/llm_provider.py`. All
  configuration writes now go through one generic Groundskeeping write flow over
  `GroundworkersConfigMutationService`.
- Reading `[tools.omop_graph]` and `[tools.omop_emb]`. Groundworkers reads only its
  own package section plus the named entries it references. Graph availability now
  follows the resolved CDM database, so a CDM-only stack gets graph and lexical
  grounding without an empty `[tools.omop_graph]` section.

### Changed — configuration

- `[tools.groundworkers]` takes `cdm_db`, optional `embedding_model_name`, and
  optional `vector_store_name` as references to named stack entries.
- `grounding.max_depth` (1-10, default 5) is now configurable; it was hardcoded.
- `grounding` no longer carries `embedding_model_name`; it is a top-level reference.
- `describe()` is keyed by `database`, `model`, and `vector_store`. Passwords and
  API keys are masked and safe URLs carry no credentials.

### Changed — payloads

- **Strict concept flags.** Grounding results report `standard_concept` true only
  for raw `standard_concept = 'S'` and add `classification_concept`, true only for
  `'C'`. omop-graph 2.x projects a single combined boolean for both, so the raw flag
  is read from the CDM at the adapter boundary. Previously classification concepts
  (ATC and CPT4 hierarchy nodes) were reported as standard and therefore looked like
  valid CDM mapping targets. Verified against a live vocabulary: grounding
  `metformin` returns RxNorm `metformin` as standard and ATC `metformin; oral` as
  classification.
- Concept views and grounding results carry `is_active`, from omop-graph's
  normalized activity field (blank and whitespace-only `invalid_reason` count as
  active).
- `concept_ground` accepts `standard_only` and `active_only` (both default `false`),
  which narrow candidate resolution, not returned results.
- `grounding_explanation` adds `standard_only`, `active_only`, and
  `embedding_tier_detail`.

### Fixed

- **The embedding grounding tier never returned candidates.** The read-oriented
  graph is built with `write=False`, and omop-graph derives its on-demand query
  encoder from the *writer* interface, so the resolver ran and matched nothing while
  status reported embeddings active. Groundworkers now encodes the query itself with
  the configured `ModelBackend` and `EmbeddingRole.QUERY` — a read-only call that
  never writes vectors — and `embedding_resolver_active` requires that encoder.
- An embedding configuration the graph rejected failed the whole backend instead of
  falling back to lexical grounding. Construction now retries without it, reports
  the exception type only, and keeps lexical tiers available. An unavailable
  embedding tier is skipped per-request and reported in `embedding_tier_detail`.
- The setup console displayed omop-graph's `max_depth`/`max_paths`, which became
  per-call arguments in 2.x and were therefore inert. It now shows Groundworkers'
  own CDM database, vocabulary schema, and grounding policy.

### Dependencies

- `omop-alchemy>=1,<2` and `numpy` are now declared directly; both were imported
  without declaration.
- `tui` extra additionally declares `textual` and `rich`, which the setup pages
  import directly.
- New `embedding-artifacts` extra for `h5py`; that import is now lazy so a core
  install can import the module.

## 0.3.4
- started building out the configuration TUI 
- lock upper boundary for oa_configurator compatibility

## 0.3.3

feature: changed the way that knowledge packs are registered, updated baseline knowledge packs (status is very draft for this part - ymmv)

## 0.3.2

fix: repaired integration tests

## 0.3.1

chore: version bump

## 0.3.0

feat: knowledge-base integration, layer cleanup, oa-configurator integration, graph and embedding services
fix: candidate-bundle metadata, knowledge content-serving, embedding-backend honesty, source-profile signals

- populate identity metadata (`concept_code`, `vocabulary_id`, `domain_id`, `concept_class_id`) on embedding-only candidates in `concept_candidate_bundle` via a single batch `concept_views` backfill (omop-emb never carries these); warn when the graph service is unavailable to enrich them
- finish the knowledge catalogue: add `KnowledgeCatalogue.get_pack()` and a `knowledge_pack` MCP tool that serve pack `guidance`/`rules`/`examples` content — previously only manifest metadata and filenames were exposed; also collapse the dead tail branch in `PackApplicability.matches`
- reject an unsupported embedding backend eagerly at build time with an actionable message, clarifying that FAISS is a query-time cache accelerator (`faiss_cache_dir` + the `embedding-faiss` extra), not a standalone backend value
- record `inferred_vocab` on source-vocabulary columns so the router derives a table-level domain hint from them, and surface the matched source profile's `structural_skip_field_types` and `packed_value_column_hint` on `PreIngestBundle` / the assisted-plan REST response instead of discarding them
- require `omop-emb>=1.1.1`
- add knowledge-base catalogue integration and expand the knowledge-facing tool and service surface
- introduce dedicated graph and grounding services so concept and resolver tools delegate through a cleaner service-layer split
- integrate `oa-configurator` into application/bootstrap wiring and refresh the example configuration path
- expand the `omop-graph` and `omop-emb` adapter layer, including parentless domain-constrained grounding support via `omop-graph>=1.3.0`
- refresh architecture and service documentation, including new graph, grounding, resolver, and source-planning pages

## 0.2.0

feat: add direct Python service layer, mapping workflows, and docs refresh

- add `build_application()` and a shared application container for direct Python consumers
- add `MappingService` as a reusable service-layer API for mapping-oriented workflows
- add mapping tools for normalized search, candidate bundles, parent backoff, mapping context, `Maps to value`, mapping-expression resolution, and candidate evaluation
- keep mapping MCP tools thin by delegating orchestration to the service layer
- tighten `omop-emb` typing through the adapter and server composition path
- extend docs to cover MCP and direct-Python integration, layer boundaries, and mapping workflows

## 0.1.1

feat: add chunk coherence pass and review-state transitions

- add coherence reranking based on approved-set distribution
- infer provisional sets for inferred chunks
- transition processed chunks to REVIEW and skip them on resume
- add unit coverage for coherence behavior

## 0.1.0

- alpha release for review
