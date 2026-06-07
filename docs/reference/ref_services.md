# Services — API Reference

## Application Composition

`build_application(config)` is the shared composition root for direct Python
consumers. It builds the adapters, the service container, and returns a
`GroundworkersApp` with both attached.

::: groundworkers.app

## VocabService

`VocabService` provides vocabulary search and concept navigation over OMOP CDM
vocabulary tables. It is the backing service for the search MCP tools and a
dependency of `MappingService`.

::: groundworkers.services.vocab

## MappingService

`MappingService` is the direct-Python API for mapping workflows. The mapping MCP
tools delegate to this service rather than implementing orchestration in the tool
module.

::: groundworkers.services.mapping

## TextService

`TextService` provides LLM-backed clinical text preprocessing. The text MCP tools
(`text_normalize`, `text_decompose`, `text_disambiguate`) delegate to this service.

::: groundworkers.services.text
