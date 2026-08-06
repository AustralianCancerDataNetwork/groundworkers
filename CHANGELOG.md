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
