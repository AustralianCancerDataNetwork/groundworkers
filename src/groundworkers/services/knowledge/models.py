"""Data models for knowledge pack manifests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


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

    def matches(
        self,
        *,
        source_system: str | None = None,
        section_names: list[str] | None = None,
        domains: list[str] | None = None,
    ) -> bool:
        if self.always:
            return True
        # Only exclude on a dimension when the caller provided context AND it doesn't match.
        # None context means "don't filter on this dimension" (discovery / unfiltered query).
        if self.source_system and source_system is not None and source_system != self.source_system:
            return False
        if self.domains and domains is not None:
            if not any(d in domains for d in self.domains):
                return False
        if self.section_name_patterns and section_names is not None:
            if not any(
                re.search(pattern, name, re.IGNORECASE)
                for pattern in self.section_name_patterns
                for name in section_names
            ):
                return False
        # Determine whether any condition could actually be evaluated with the provided context.
        # If none can (all context is None), we are in discovery mode — include the pack.
        has_evaluable = (
            (self.source_system and source_system is not None)
            or (self.domains and domains is not None)
            or (self.section_name_patterns and section_names is not None)
        )
        if not has_evaluable:
            return True
        # At least one evaluable condition must be a positive match.
        return bool(
            (self.source_system and source_system == self.source_system)
            or (self.domains and domains and any(d in domains for d in self.domains))
            or (
                self.section_name_patterns
                and section_names
                and any(
                    re.search(p, n, re.IGNORECASE)
                    for p in self.section_name_patterns
                    for n in section_names
                )
            )
        )


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
