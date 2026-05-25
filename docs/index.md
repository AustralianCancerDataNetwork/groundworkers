# groundworkers

**groundworkers** is the atomic, read-only MCP (Model Context Protocol) tool substrate for
the CAVA stack.  It exposes OMOP vocabulary lookups, embedding similarity search, cohort
concept references, and system status as typed MCP tools that any MCP client can call —
including [groundcrew](https://github.com/AustralianCancerDataNetwork/groundcrew),
Claude Code, and autonomous agents.

## Why groundworkers?

- **Stateless and read-only**: groundworkers never mutates data.  All session state lives
  in the caller (groundcrew or equivalent).  This makes groundworkers safe to run as a
  shared service across multiple clients and mapping sessions.
- **Thin adapter layer**: rather than reimplementing vocabulary logic, groundworkers delegates
  to proven libraries — [omop-graph](https://australiancancerdatanetwork.github.io/omop-graph/)
  for concept and hierarchy queries, [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/)
  for embedding search, and OpenAnalytics cohort databases for concept usage data.
- **Structured errors**: every tool returns a plain dict.  Errors use a consistent
  `{"error": true, "code": "...", "message": "..."}` shape so clients can handle them
  without inspecting exception types.
- **Deployable anywhere**: launch locally as a stdio subprocess for development or deploy
  as an HTTP server for team-wide access.  No code changes.

---

## Documentation Overview

### Tools
- [Overview](tools/overview.md): All registered tools, error codes, and response shapes.
- [Concept Tools](tools/concept.md): OMOP concept lookup, hierarchy, path finding, and free-text grounding.
- [Search Tools](tools/search.md): Agent-composable search primitives with raw quality signals.
- [Embedding Tools](tools/embedding.md): Embedding index status, neighbour search, and on-the-fly search.
- [Cohort Tools](tools/cohort.md): Cohort concept reference queries.
- [System Tools](tools/system.md): Adapter availability and vocabulary catalogue.

### Adapters
- [OmopGraph](adapters/omop_graph.md): Concept and hierarchy queries via omop-graph.
- [OmopEmb](adapters/omop_emb.md): Embedding index queries via omop-emb.
- [OaCohorts](adapters/oa_cohorts.md): Cohort database queries.

### Relation to groundcrew

!!! info
    groundworkers is the **tool substrate**.  It provides atomic vocabulary operations.
    [groundcrew](https://github.com/AustralianCancerDataNetwork/groundcrew) is the
    **orchestration layer** that sequences those tool calls into a stateful multi-step
    mapping workflow.  groundcrew connects to groundworkers over the MCP protocol and
    never imports it as a Python library.
