# Services — API Reference

## Application Composition

`build_application(config)` is the shared composition root for direct Python consumers. It builds the adapters, the service container, and returns a `GroundworkersApp` with both attached.

::: groundworkers.app

## VocabService

`VocabService` provides vocabulary search and concept navigation over OMOP CDM vocabulary tables. It is the backing service for the search MCP tools and a dependency of `MappingService`.

::: groundworkers.services.vocab

## GraphService

`GraphService` is the direct-Python surface for deterministic graph-backed concept lookup, hierarchy traversal, path finding, standard mapping, and neighbor exploration.

::: groundworkers.services.graph

## ConceptGroundingService

`ConceptGroundingService` owns the caller-facing grounding policy over `GraphService`: resolver tier ordering, ancestry constraints, and grounding explanations.

::: groundworkers.services.grounding

## MappingService

`MappingService` is the direct-Python API for mapping workflows. The mapping MCP tools delegate to this service rather than implementing orchestration in the tool module.

::: groundworkers.services.mapping

## TextService

`TextService` provides LLM-backed clinical text preprocessing. The text MCP tools (`text_normalize`, `text_decompose`, `text_disambiguate`) delegate to this service.

::: groundworkers.services.text

## DomainService

`DomainService` provides LLM-backed batch OMOP domain classification for structured field labels and example values. The `domain_classify` MCP tool delegates to this service.

::: groundworkers.services.domain

## SourcePlanningService

`SourcePlanningService` provides stateless source-analysis and assisted planning workflows for pre-ingest artifacts.

::: groundworkers.services.source_planning

## Knowledge Catalogue

The catalogue is a standalone discovery service rather than an entry in `app.services`. It reads bundled and configured packs and returns applicable manifest/content objects.

::: groundworkers.services.knowledge.catalogue

## SemanticProjectionService

`SemanticProjectionService` deterministically projects a grounded concept into one or more CDM rows via `omop-semantics`. Not part of `app.services` — see [SemanticProjectionService](../services/semantic_projection.md) for why.

::: groundworkers.services.semantic_projection
