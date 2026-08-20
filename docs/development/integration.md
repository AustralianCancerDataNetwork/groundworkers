# Integrating groundworkers

How to drive Groundworkers from Python against the same steady-state
configuration the setup console produces. Nothing here imports Textual,
Groundskeeping, or another application's setup code.

## Build the runtime

```python
from groundworkers.app import build_application
from groundworkers.bootstrap import build_app_config

config = build_app_config(config_path="/path/to/config.toml")
app = build_application(config)
```

`build_app_config` resolves the stack and returns an `AppConfig` holding only
Groundworkers-owned values:

| Attribute | Type |
|---|---|
| `groundworkers` | `GroundworkersConfig` |
| `cdm_database` | `ResolvedCDMDatabase` |
| `cdm_engine`, `vocabulary_engine` | `sqlalchemy.Engine` |
| `embedding_model` | `ResolvedModel \| None` |
| `vector_store` | `ResolvedVectorStore \| None` |

Omitting `config_path` uses `OA_CONFIG_PATH` or the default stack location.

## Available services

`app.services` attributes are `None` only when their prerequisites are absent, so
check before use:

```python
services = app.services

services.vocab          # always, with a CDM database
services.graph          # always, with a CDM database
services.grounding      # always, with a CDM database
services.mapping        # always, with a CDM database
services.text           # only when groundworkers.llm_model_name resolves
services.domain         # only when groundworkers.llm_model_name resolves
services.source_planning
```

A CDM-only configuration is fully supported. Graph availability follows the
resolved CDM database — there is no separate section to enable.

## Grounding free text

```python
result = app.services.grounding.ground(
    "type 2 diabetes",
    limit=10,
    domain="Condition",
    vocabulary_id=None,
    parent_ids=None,        # optional required-ancestor constraint
    standard_only=False,    # narrows candidate resolution, not results
    active_only=False,
)

for hit in result["results"]:
    if hit["standard_concept"]:
        print(hit["concept_id"], hit["concept_name"])
    elif hit["classification_concept"]:
        # A real hierarchy node, but not a valid CDM entity-field target.
        print("classification:", hit["concept_id"], hit["concept_name"])
```

`limit` must be positive; a non-positive value raises `GroundworkersError` with
code `INVALID_INPUT`.

### Interpreting the explanation

```python
explanation = result["grounding_explanation"]
explanation["matched_tier"]           # EXACT | FULLTEXT | EMBEDDING_NEAREST | PARTIAL
explanation["used_embedding"]         # bool
explanation["embedding_tier_detail"]  # None, or why the embedding tier was skipped
```

Tiers run in order and the first one that yields results wins. When
`embedding_tier_detail` is set, the embedding tier was planned but could not run —
usually an unreachable provider — and the answer came from lexical tiers alone.
Treat a set value as "this result may be incomplete".

## Error handling

Every service raises `GroundworkersError` with a stable code:

```python
from groundworkers.base.errors import GroundworkersError

try:
    concept = app.services.graph.get_concept(201826)
except GroundworkersError as exc:
    print(exc.code, exc.message)   # NOT_FOUND | INVALID_INPUT | BACKEND_UNAVAIL | QUERY_ERROR | …
```

Messages are safe to log and surface: provider and database failures are
translated so they do not carry credentials or connection strings.

## Concept flag contract

| Field | Meaning |
|---|---|
| `standard_concept` | true only for raw `standard_concept = 'S'` |
| `classification_concept` | true only for raw `standard_concept = 'C'` |
| `is_active` | `invalid_reason` unset, treating blank and whitespace-only as active |

Do not derive standardness from omop-graph's `ConceptView.standard_concept`: that
field is a single boolean covering both `'S'` and `'C'`. Groundworkers reads the
raw flag at its adapter boundary so callers get the distinction.

## Writing configuration

Configuration writes are a local console concern, not part of the integration
surface. If you are embedding the setup console in another host application, drive
`GroundworkersConfigMutationService` through Groundskeeping's
`ConfigWizardController`:

```python
from groundskeeping.configurator import ConfigWizardController, MutationOperation

from groundworkers.application.setup.configuration_provider import (
    CDM_SETUP_TARGET,
    GroundworkersConfigMutationService,
    cdm_setup_workflow,
)
from groundworkers.application.setup.model_inventory import discover_provider_models

service = GroundworkersConfigMutationService(
    "/path/to/config.toml",
    model_discoverer=discover_provider_models,
)
operation = (
    MutationOperation.UPDATE
    if service.capabilities(CDM_SETUP_TARGET, MutationOperation.UPDATE).supported
    else MutationOperation.CREATE
)
controller = ConfigWizardController(cdm_setup_workflow(operation), service)
```

The same shape covers `MODEL_SETUP_TARGET` / `model_setup_workflow` and
`LLM_SETUP_TARGET` / `llm_setup_workflow`. The provider decides create-versus-update
from the current file, owns revision checking and redaction, and returns
`conflicted` rather than clobbering another writer. `model_discoverer` is the
injected seam for live model inventory; supply your own to avoid network calls in
tests.

This requires the `tui` extra for Groundskeeping, but not Textual — the provider
and controller are host-agnostic.
