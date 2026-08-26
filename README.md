# groundworkers

Groundworkers is the reusable, read-only capability layer for OMOP vocabulary lookup, concept mapping, source planning, and related analysis. It combines deterministic services with optional embeddings and model-assisted operations.

It does not own patient-level writes, session state, or job lifecycle. Those concerns belong to the calling application; `groundcrew` is the usual stateful orchestrator.

## Choose an interface

| Interface | Use it when |
| --- | --- |
| MCP | An agent needs discoverable tools, prompts, resources, and a shared remote runtime. |
| REST | An application needs the curated HTTP workflows for candidate bundles or assisted source planning. |
| Python | A process owns batching, evaluation, or orchestration and can run in-process. |

Start with the [documentation home](docs/index.md), or choose a path directly:

- [Concepts and capability choices](docs/concepts.md) explains retrieval, grounding, mapping, source planning, and projection.
- [Installation](docs/usage/installation.md) covers package extras and prerequisites.
- [Initial local setup](docs/from-scratch.md) walks through a fresh CDM-backed deployment.
- [Integrations](docs/usage/integrations.md) shows MCP, REST, and Python calls.
- [Configuration](docs/usage/configuration.md) explains shared-stack references and optional capabilities.
- [Tools overview](docs/tools/overview.md) maps jobs to the discoverable MCP surface.

## Quick start

Install the package and configure the shared OMOP stack:

```bash
pip install groundworkers
omop-config configure groundworkers
```

Inspect the active configuration and MCP surface before starting a service:

```bash
groundworkers --describe
```

Run MCP over stdio for a client that starts the process, or over Streamable HTTP for a shared service:

```bash
groundworkers
groundworkers --transport streamable-http --host 127.0.0.1 --port 8000
```

REST is a separate, curated interface:

```bash
groundworkers --transport rest --host 127.0.0.1 --port 8080
```

## Direct Python

Build the application once and reuse it. Services are optional when their prerequisites are not configured:

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config()
app = build_application(config)

bundle = app.services.mapping.concept_candidate_bundle(
    "type 2 diabetes",
    domain="Condition",
)
```

The `mapping` service in this example requires a configured CDM database. Embedding-backed channels require an embedding model and vector store; text, domain classification, and assisted source planning require a configured chat model. Use `groundworkers --describe` to see what the current deployment exposes.

## Companion repositories

- [groundcrew](https://github.com/AustralianCancerDataNetwork/groundcrew) owns session state and orchestration.
- [omop-graph](https://australiancancerdatanetwork.github.io/omop-graph/) provides graph-backed OMOP traversal.
- [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/) provides embedding stores and indexes.
