# Services — API Reference

## Application Composition

`build_application(config)` is the shared composition root for direct Python
consumers. It builds the adapters, the service container, and returns a
`GroundworkersApp` with both attached.

::: groundworkers.app

## MappingService

`MappingService` is the direct-Python API for mapping workflows. The mapping MCP
tools delegate to this service rather than implementing orchestration in the tool
module.

::: groundworkers.services.mapping
