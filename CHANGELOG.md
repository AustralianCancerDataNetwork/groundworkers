## 0.3.0

feat: knowledge-base integration, layer cleanup, oa-configurator integration, graph and embedding services

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
