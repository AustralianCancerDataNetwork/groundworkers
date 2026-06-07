# groundworkers

`groundworkers` is a read-only OMOP vocabulary integration package. You can use it
in two ways:

- as an **MCP server** for tool consumers such as `groundcrew`, Claude Code, and
  other MCP clients
- as a **Python library** for applications that want to call mapping and retrieval
  logic directly

No patient-level writes. No session state. No transport-specific business logic.

## When to use it

Use `groundworkers` when you want:

- OMOP concept lookup and hierarchy navigation
- exact, full-text, and embedding-based concept retrieval
- mapping-oriented evidence bundles and context assembly
- one package that works both over MCP and in-process from Python

## How it is organized

```mermaid
flowchart LR
    Client1[Python app] --> App[build_application]
    Client2[MCP client] --> Server[groundworkers server]
    App --> Services[services/]
    Server --> Tools[tools/]
    Server --> Prompts[prompts/]
    Tools --> Services
    Services --> Adapters[adapters/]
    Adapters --> OG[omop-graph]
    Adapters --> OE[omop-emb]
    Adapters --> DB[(OMOP DB)]
    Adapters --> LM[LLM API]
```

- `adapters/` handle dependency-specific details
- `services/` handle reusable workflow logic
- `tools/` expose MCP-facing wrappers
- `app.py` and `server.py` wire those pieces together

## What it exposes

| Group | Surface | Notes |
|---|---|---|
| Concept | `concept_get`, `concept_by_code`, `concept_ancestors`, `concept_descendants`, `concept_relationships`, `concept_equivalency_path`, `concept_path`, `concept_map_to_standard`, `concept_neighbors` | Backed by `OmopGraphAdapter` |
| Resolver | `concept_ground` | Best-answer grounding pipeline |
| Search | `concept_search_exact`, `concept_search_fulltext`, `concept_navigate_to_standard` | Low-level lexical primitives |
| Mapping | `concept_search_normalized`, `concept_candidate_bundle`, `concept_parent_backoff`, `concept_mapping_context`, `concept_map_to_value`, `concept_resolve_mapping_expression`, `mapping_evaluate_candidates` | High-level mapping workflows |
| Embedding | `embedding_index_status`, `embedding_neighbours`, `embedding_search`, `embedding_encode` | Backed by `OmopEmbAdapter` |
| Text tools | `text_normalize`, `text_decompose`, `text_disambiguate` | LLM-backed clinical text preprocessing via `TextService`; registered when `llm` is configured |
| Text prompts | `normalize_clinical_term`, `decompose_clinical_text`, `disambiguate_clinical_term` | MCP prompts exposing the same LLM message sequences; registered alongside text tools |
| System | `system_status`, `system_vocabulary_catalogue` | Always registered |

## Quick start

### MCP server

```bash
uv venv
uv sync --extra dev --extra embedding-tools
uv run groundworkers --config config/groundworkers.example.yaml --describe
uv run groundworkers --config config/groundworkers.example.yaml
```

### Direct Python use

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.model_validate(
    {
        "omop_graph": {
            "db_url": "postgresql+psycopg://user:pass@localhost:5432/omop",
            "vocab_schema": "omop_vocab",
        },
        "omop_emb": {
            "enabled": True,
            "backend_type": "pgvector",
            "db_url": "postgresql+psycopg://user:pass@localhost:5432/omop",
            "default_model_name": "qwen3-embedding:0.6b",
            "api_base": "http://localhost:11434/v1",
            "api_key": "ollama",
        },
    }
)

app = build_application(config)
mapping = app.services.mapping
assert mapping is not None

bundle = mapping.concept_candidate_bundle(
    "type 2 diabetes",
    domain="Condition",
    include_normalized=True,
    include_fulltext=True,
    include_embedding=True,
)
```

## Example config

```yaml
omop_graph:
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  vocab_schema: omop_vocab

omop_emb:
  enabled: true
  backend_type: pgvector
  db_url: "postgresql+psycopg://user:pass@localhost:5432/omop"
  default_model_name: qwen3-embedding:0.6b
  api_base: "http://localhost:11434/v1"
  api_key: "ollama"

llm:
  api_base: "https://api.openai.com/v1"
  api_key: "sk-..."
  default_model_name: "gpt-4o-mini"
```

## End-to-end examples

### MCP consumer flow

```mermaid
sequenceDiagram
    participant C as MCP consumer
    participant GW as groundworkers
    participant M as MappingService
    participant D as OMOP dependencies

    C->>GW: call tool concept_candidate_bundle
    GW->>M: invoke tool wrapper
    M->>D: gather lexical, graph, and embedding evidence
    D-->>M: candidate evidence
    M-->>GW: assembled bundle
    GW-->>C: MCP-safe JSON result
```

Representative tool payload:

```json
{
  "tool": "concept_candidate_bundle",
  "arguments": {
    "query": "type 2 diabetes",
    "domain": "Condition",
    "include_normalized": true,
    "include_fulltext": true,
    "include_embedding": true,
    "include_standard_mappings": true
  }
}
```

### Direct Python flow

```mermaid
sequenceDiagram
    participant App as Python application
    participant S as MappingService
    participant A as Adapters
    participant D as OMOP dependencies

    App->>S: concept_mapping_context(...)
    S->>A: coordinate graph / vocab / emb calls
    A->>D: execute dependency queries
    D-->>A: raw results
    A-->>S: adapter-shaped results
    S-->>App: domain result
```

## If you are using it as a library

Start with `build_application(config)` and `app.services.mapping` for higher-level
mapping workflows. Use `app.services.text` for LLM-backed normalization, decomposition,
and disambiguation before passing terms into search or grounding. Drop down to
`app.adapters.*` when you want lower-level, dependency-shaped operations.

## Companion repos

- [groundcrew](https://github.com/AustralianCancerDataNetwork/groundcrew) for MCP-based orchestration
- [omop-graph](https://australiancancerdatanetwork.github.io/omop-graph/) for OMOP concept and hierarchy queries
- [omop-emb](https://australiancancerdatanetwork.github.io/omop-emb/) for embedding index and semantic retrieval
