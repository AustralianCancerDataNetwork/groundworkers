# Knowledge tools

Knowledge packs are reusable guidance and rule bundles. Groundworkers ships baseline packs and can layer site-specific or localisation packs from `knowledge_packs_root`. They are discovered independently of source planning, although a source-planning result often provides useful context for the query.

The knowledge tools are registered when at least one packs root is available. `knowledge_catalogue` is the discovery step; `knowledge_pack` loads the content of a selected pack.

## `knowledge_catalogue`

Find packs whose applicability matches the supplied context. Omit filters to list all discovered packs.

```json
{
  "source_system": "redcap",
  "domains": ["Condition"],
  "section_names": ["medical_history"],
  "include_local": true
}
```

The response contains manifest metadata, including the pack name, layer, version, scope, mechanisms, and applicability. A configured pack with the same layer and name as a bundled pack takes precedence.

## `knowledge_pack`

Load a selected pack by name.

```json
{"name": "domain-routing"}
```

The result includes the manifest and any readable `guidance.md`, `rules.yaml`, and `examples.yaml` content. A missing pack returns `NOT_FOUND`; a missing optional content file does not make the whole pack unavailable.

## Direct Python use

The underlying `KnowledgeCatalogue` is useful when a Python application needs discovery without MCP:

```python
from pathlib import Path

from groundworkers.services.knowledge import KnowledgeCatalogue

catalogue = KnowledgeCatalogue(Path("/path/to/knowledge"))
packs = catalogue.query(source_system="redcap", domains=["Condition"])
selected = catalogue.get_pack(packs[0].name) if packs else None
```

Knowledge packs provide context. They do not themselves ground a concept, write a CDM row, or carry session state.
