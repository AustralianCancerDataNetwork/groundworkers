# Extending groundworkers

This guide is for contributors adding new adapters, services, MCP tools, or REST endpoints. The main rule is simple:

**keep business logic above the transport layer and keep dependency details below it.**

## Current extension points

| If you are adding... | Put it in... |
|---|---|
| A wrapper around one external dependency | `src/groundworkers/adapters/` |
| Reusable domain logic coordinating adapters | `src/groundworkers/services/` |
| An MCP tool surface | `src/groundworkers/tools/` |
| A curated HTTP route | `src/groundworkers/transports/rest/` |
| Runtime config resolution | `src/groundworkers/bootstrap.py` |
| Package-owned config fields | `src/groundworkers/config.py` |

## Adding a new adapter

Use an adapter when the main job is to wrap one external system cleanly.

Adapter expectations:

- accept already-resolved handles or explicit constructor values
- do not read TOML or environment variables directly
- expose dependency-shaped operations with predictable Python errors
- raise `GroundworkersError` for backend-facing failures that callers should
  reason about

Good adapter candidates:

- a new OMOP-adjacent query layer
- a new read-only store for reusable knowledge
- a new model backend wrapper

Poor adapter candidates:

- transport-specific request parsing
- orchestration across multiple dependencies
- policy about when a caller should prefer one retrieval strategy over another

## Adding a new service

Use a service when the capability contains reusable domain logic that a Python caller should be able to use directly.

Service expectations:

- be transport-agnostic
- coordinate one or more adapters
- hold workflow logic, scoring, routing, or packaging behavior
- return normal Python results, not MCP-style error dicts

Typical pattern:

1. add or extend the adapter operations you need
2. add the domain logic in `services/`
3. wire the service in `build_application(...)`
4. expose it through MCP or REST only if the capability should be transported

## Adding an MCP tool

Add an MCP tool when the capability should participate in tool discovery or be available to remote clients.

Tool expectations:

1. validate and clamp inputs
2. call a service
3. call an adapter directly only when the capability is intentionally adapter-shaped and there is no service abstraction
4. translate exceptions into MCP-safe error dicts

Do not put the main business logic in the tool wrapper itself.

## Adding a REST endpoint

REST is curated. Do not mirror every MCP tool automatically.

Add a REST route when:

- the workflow has a stable request/response contract
- a typed HTTP client is a likely consumer
- the route is better understood as an application operation than as a generic tool

Route expectations:

- request and response models live in `transports/rest/models.py`
- routes call services directly, not MCP wrappers
- transport-specific error mapping stays in `transports/rest/api.py`

## Updating configuration

Before adding a new config field, decide which package actually owns that concern.

Use this rough split:

- `omop-alchemy`: CDM and vocabulary models
- `omop-graph`: graph traversal behavior
- `omop-emb`: embedding store and embedding model settings
- `groundworkers`: transport defaults, LLM worker behavior, source-planning behavior, knowledge-pack behavior

If the setting belongs to `groundworkers`, add it to `GroundworkersConfig` in `config.py` and let `bootstrap.py` thread it into the runtime.

## Testing new capabilities

Preferred test seams:

- use `build_app_config_from_stack(...)` for runtime bootstrap tests
- unit-test services with fake adapters where practical
- test MCP registration through `create_server(...)`
- test REST routes with `fastapi.testclient.TestClient`

The goal is to keep each layer testable without needing the full deployment stack.

## Practical checklist

When a new capability is ready, check:

- config resolution happens in `bootstrap.py`, not in the adapter
- the adapter is config-agnostic
- the service is transport-agnostic
- MCP and REST wrappers stay thin
- docs describe the current interface and a real use case
