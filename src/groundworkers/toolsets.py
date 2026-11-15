"""Server-side toolset names, metadata and selection."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Toolset(StrEnum):
    """Built-in toolsets; plugins may also supply their own string names."""

    SYSTEM = "system"
    LOOKUP = "lookup"
    SEARCH = "search"
    MAPPING = "mapping"
    MAPPING_ADVANCED = "mapping_advanced"
    GRAPH = "graph"
    EMBEDDING = "embedding"
    LLM = "llm"
    SOURCE = "source"
    KNOWLEDGE = "knowledge"
    SEMANTIC = "semantic"
    OFFLINE = "offline"


TOOLSET_DESCRIPTIONS: Mapping[str, str] = MappingProxyType(
    {
        Toolset.SYSTEM: "Deployment status and vocabulary catalogue; always registered.",
        Toolset.LOOKUP: "Fetch a concept by id or by vocabulary code.",
        Toolset.SEARCH: "Find candidate concepts for text.",
        Toolset.MAPPING: "Source-to-standard mapping workflow: candidates, mapping context, hierarchy checks, standard targets.",
        Toolset.MAPPING_ADVANCED: "Less common mapping utilities: mapping by vocabulary code, value mapping, mapping expressions, shared standard ancestors.",
        Toolset.GRAPH: "Concept hierarchy, relationships and paths.",
        Toolset.EMBEDDING: "Embedding index search and neighbours.",
        Toolset.LLM: "Language-model text helpers.",
        Toolset.SOURCE: "Source-file planning.",
        Toolset.KNOWLEDGE: "Convention packs.",
        Toolset.SEMANTIC: "Semantic projection (optional).",
        Toolset.OFFLINE: "Evaluation and diagnostics, not intended for agents.",
    }
)


@dataclass(frozen=True)
class ToolMeta:
    """Metadata retained by the server for a registered tool."""

    toolsets: frozenset[str]


def select_tools(
    tool_toolsets: Mapping[str, Collection[str]],
    include: Collection[str] | None = None,
    exclude: Collection[str] = (),
) -> frozenset[str]:
    """Select tools sharing any included toolset; exclusion always wins."""
    included = frozenset(include) if include is not None else None
    excluded = frozenset(exclude)
    return frozenset(
        name
        for name, memberships in tool_toolsets.items()
        if (included is None or not included.isdisjoint(memberships))
        and excluded.isdisjoint(memberships)
    )
