"""Knowledge catalogue — discovers and filters pack manifests.

The catalogue reads from a packs/ directory tree structured as:
    packs/
        {layer}/
            {pack-name}/
                manifest.yaml
                rules.yaml        (optional)
                guidance.md       (optional)
                examples.yaml     (optional)

The catalogue is the single entry point for any agent or pipeline stage
that needs to discover which knowledge packs apply to a given job context.
It can be called at any phase — not only at source planning time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from groundworkers.services.knowledge.models import (
    KnowledgeLayer,
    PackApplicability,
    PackManifest,
)

logger = logging.getLogger(__name__)

_KNOWN_LAYERS: frozenset[str] = frozenset({"core", "specialisation", "source", "localisation"})


class KnowledgeCatalogue:
    """Discovers pack manifests under a packs root directory and filters by context."""

    def __init__(self, packs_root: Path) -> None:
        self._packs_root = packs_root
        self._cache: list[PackManifest] | None = None

    def query(
        self,
        *,
        source_system: str | None = None,
        domains: list[str] | None = None,
        section_names: list[str] | None = None,
        layer: KnowledgeLayer | None = None,
        include_local: bool = True,
    ) -> list[PackManifest]:
        """Return manifests whose applicability conditions match the given context."""
        manifests = self._load()
        results = []
        for manifest in manifests:
            if layer is not None and manifest.layer != layer:
                continue
            if not include_local and manifest.layer == "localisation":
                continue
            if manifest.applicability.matches(
                source_system=source_system,
                section_names=section_names,
                domains=domains,
            ):
                results.append(manifest)
        return results

    def all(self) -> list[PackManifest]:
        """Return all discovered manifests without filtering."""
        return list(self._load())

    def invalidate(self) -> None:
        """Clear the manifest cache, forcing a re-read on next query."""
        self._cache = None

    def _load(self) -> list[PackManifest]:
        if self._cache is not None:
            return self._cache
        manifests: list[PackManifest] = []
        if not self._packs_root.exists():
            self._cache = manifests
            return manifests
        for layer_dir in sorted(self._packs_root.iterdir()):
            if not layer_dir.is_dir() or layer_dir.name not in _KNOWN_LAYERS:
                continue
            for pack_dir in sorted(layer_dir.iterdir()):
                manifest_file = pack_dir / "manifest.yaml"
                if not manifest_file.exists():
                    continue
                try:
                    manifest = _parse_manifest(manifest_file, pack_dir)
                    manifests.append(manifest)
                except Exception as exc:
                    # A malformed manifest (bad YAML, missing required key, invalid
                    # section_name_pattern regex, ...) skips only that pack — other
                    # packs and queries are unaffected.
                    logger.warning("Skipping malformed knowledge pack %s: %s", manifest_file, exc)
        self._cache = manifests
        return manifests


def _parse_manifest(manifest_file: Path, pack_dir: Path) -> PackManifest:
    raw: dict[str, Any] = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}

    applicability_raw = raw.get("applicability", {})
    applicability = PackApplicability(
        always=applicability_raw.get("always", False),
        source_system=applicability_raw.get("source_system"),
        section_name_patterns=applicability_raw.get("section_name_patterns", []),
        domains=applicability_raw.get("domains", []),
    )

    return PackManifest(
        name=raw["name"],
        layer=raw["layer"],
        version=str(raw.get("version", "0.1")),
        shareability=raw.get("shareability", "public"),
        scope_summary=raw.get("scope_summary", ""),
        mechanisms=raw.get("mechanisms", []),
        applicability=applicability,
        see_also=raw.get("see_also", []),
        pack_path=pack_dir,
    )
