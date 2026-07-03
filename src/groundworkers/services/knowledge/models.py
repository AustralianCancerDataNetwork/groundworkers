"""Data models for knowledge pack manifests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


KnowledgeLayer = Literal["core", "specialisation", "source", "localisation"]
KnowledgeMechanism = Literal["pre-compute", "context-inject", "post-filter", "convention-check"]
Shareability = Literal["public", "shareable", "private"]


@dataclass(frozen=True)
class PackApplicability:
    """Conditions under which a pack should be loaded for a given job.

    All non-None conditions must match for the pack to be included.
    An always=True pack is loaded unconditionally (used by core packs).
    section_name_patterns are regex patterns matched against the job's
    detected section names — used for specialty auto-detection.
    """

    always: bool = False
    source_system: str | None = None
    section_name_patterns: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Validate manifest-supplied regexes at construction so a malformed pattern
        # fails the single offending pack (skipped by the catalogue's per-pack guard)
        # rather than raising re.error at query time and breaking every query.
        for pattern in self.section_name_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid section_name_pattern {pattern!r}: {exc}") from exc

    def matches(
        self,
        *,
        source_system: str | None = None,
        section_names: list[str] | None = None,
        domains: list[str] | None = None,
    ) -> bool:
        if self.always:
            return True
        # Exclude only on a dimension the pack constrains when the caller supplied
        # context for it AND that context fails to match. A None context means
        # "don't filter on this dimension" (discovery / unfiltered query). Any pack
        # that survives every guard is a match — either it matched a supplied
        # dimension or the caller filtered on nothing this pack constrains.
        if self.source_system and source_system is not None and source_system != self.source_system:
            return False
        if self.domains and domains is not None and not any(d in domains for d in self.domains):
            return False
        if self.section_name_patterns and section_names is not None and not any(
            re.search(pattern, name, re.IGNORECASE)
            for pattern in self.section_name_patterns
            for name in section_names
        ):
            return False
        return True


@dataclass(frozen=True)
class PackManifest:
    """Parsed content of a pack's manifest.yaml."""

    name: str
    layer: KnowledgeLayer
    version: str
    shareability: Shareability
    scope_summary: str
    mechanisms: list[KnowledgeMechanism]
    applicability: PackApplicability
    see_also: list[str] = field(default_factory=list)
    pack_path: Path | None = None

    def has_file(self, filename: str) -> bool:
        return self.pack_path is not None and (self.pack_path / filename).exists()


@dataclass(frozen=True)
class PackContent:
    """A pack's manifest plus the content of its bundled files.

    ``guidance`` is the raw markdown text of ``guidance.md`` (intended for
    context injection). ``rules`` and ``examples`` are the parsed YAML content
    of ``rules.yaml`` / ``examples.yaml`` (intended for programmatic use by the
    post-filter / convention-check mechanisms). Any field is ``None`` when the
    corresponding file is absent or could not be read.
    """

    manifest: PackManifest
    guidance: str | None = None
    rules: Any = None
    examples: Any = None
