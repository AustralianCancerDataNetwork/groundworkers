"""MCP tools for the knowledge layer catalogue.

The knowledge_catalogue tool can be called at any workflow phase — source
planning, grounding, convention checking, review — to discover which
knowledge packs apply to a given job context. It is not coupled to
source_plan and intentionally lives as a standalone lookup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from groundworkers.base.server import GroundcrewServer
from groundworkers.services.knowledge.catalogue import KnowledgeCatalogue
from groundworkers.services.knowledge.models import KnowledgeLayer, PackManifest

logger = logging.getLogger(__name__)

# Prefer bundled package data so released wheels expose the knowledge surface
# without extra runtime configuration. Fall back to the repo-root path to keep
# local source-tree execution working even if the package-data copy is missing.
_BUNDLED_PACKS_ROOT: Path = Path(__file__).resolve().parent.parent / "_knowledge" / "packs"
_REPO_PACKS_ROOT: Path = Path(__file__).resolve().parents[4] / "knowledge" / "packs"
_PACKS_ROOT: Path = _BUNDLED_PACKS_ROOT if _BUNDLED_PACKS_ROOT.exists() else _REPO_PACKS_ROOT

_KNOWN_PACK_FILES = ("manifest.yaml", "rules.yaml", "guidance.md", "examples.yaml")
_KNOWN_LAYERS: tuple[KnowledgeLayer, ...] = (
    "core",
    "specialisation",
    "source",
    "localisation",
)


def _manifest_payload(m: PackManifest) -> dict[str, Any]:
    """Project a manifest to the JSON-safe dict shared by the catalogue index,
    the catalogue query tool, and the pack-content tool."""
    return {
        "name": m.name,
        "layer": m.layer,
        "version": m.version,
        "shareability": m.shareability,
        "scope_summary": m.scope_summary,
        "mechanisms": m.mechanisms,
        "applicability": {
            "always": m.applicability.always,
            "source_system": m.applicability.source_system,
            "section_name_patterns": m.applicability.section_name_patterns,
            "domains": m.applicability.domains,
        },
        "see_also": m.see_also,
        "files_present": [f for f in _KNOWN_PACK_FILES if m.has_file(f)],
    }


def _default_packs_roots(configured_root: Path | None) -> tuple[Path, ...]:
    roots: list[Path] = []
    for root in (_PACKS_ROOT, configured_root):
        if root is None or root in roots or not root.exists():
            continue
        roots.append(root)
    return tuple(roots)


def register_knowledge_tools(
    server: GroundcrewServer,
    packs_root: Path | None = None,
) -> bool:
    """Register the knowledge catalogue tools if a packs root is available.

    Returns True when the tools were registered. The server always includes the
    bundled baseline packs when present. A configured packs root adds site- or
    deployment-specific packs on top, with later duplicates overriding bundled
    entries by layer/name. If no packs root exists at all, the knowledge group
    is not registered — matching the documented rule that tool groups appear
    only when their backing store is available, rather than advertising a tool
    that returns an empty catalogue.
    """
    roots = _default_packs_roots(packs_root)
    if not roots:
        logger.info(
            "No knowledge packs roots found; knowledge_catalogue tools not registered.",
        )
        return False
    catalogue = KnowledgeCatalogue(roots)

    @server.resource(
        "knowledge://catalogue",
        description=(
            "Full index of all available knowledge packs across all namespaces "
            "(core, specialisation, source, localisation). Each entry includes "
            "name, layer, version, shareability, scope_summary, mechanisms, "
            "applicability conditions, and see_also cross-references. "
            "Read this to understand what knowledge is available before querying "
            "with knowledge_catalogue. Use knowledge_catalogue to filter by job context."
        ),
    )
    def knowledge_catalogue_resource() -> str:
        import json
        manifests = catalogue.all()
        return json.dumps({
            "packs": [_manifest_payload(m) for m in manifests],
            "total": len(manifests),
        })

    @server.tool("knowledge_catalogue")
    def knowledge_catalogue(
        source_system: str | None = None,
        domains: list[str] | None = None,
        section_names: list[str] | None = None,
        layer: str | None = None,
        include_local: bool = True,
    ) -> dict[str, Any]:
        """Discover which knowledge packs apply to a given job context.

        Returns a filtered list of pack manifests from the knowledge layer.
        Can be called at any workflow phase — source planning, grounding,
        convention checking, or review — not only at load time.

        source_system   Filter to packs applicable to this detected source
                        system (e.g. "redcap"). Pass the detected_source_system
                        value from source_plan output.
        domains         Filter to packs relevant to these OMOP domains
                        (e.g. ["Drug", "Measurement"]).
        section_names   Section names from the job, used for specialty
                        auto-detection via manifest fingerprints.
        layer           Restrict to one namespace: "core", "specialisation",
                        "source", or "localisation".
        include_local   Whether to include localisation packs (default true).

        Each result includes: name, layer, version, shareability,
        scope_summary, mechanisms, applicability, see_also.
        see_also lists related packs worth loading alongside this one.
        """
        resolved_layer: KnowledgeLayer | None = None
        if layer is not None:
            if layer not in _KNOWN_LAYERS:
                return {
                    "error": True,
                    "code": "INVALID_INPUT",
                    "message": (
                        "layer must be one of: core, specialisation, "
                        "source, localisation"
                    ),
                }
            resolved_layer = cast(KnowledgeLayer, layer)
        try:
            results = catalogue.query(
                source_system=source_system,
                domains=domains,
                section_names=section_names,
                layer=resolved_layer,
                include_local=include_local,
            )
            return {
                "packs": [_manifest_payload(m) for m in results],
                "total": len(results),
            }
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("knowledge_pack")
    def knowledge_pack(name: str) -> dict[str, Any]:
        """Fetch the full content of one knowledge pack by name.

        Discover applicable packs with ``knowledge_catalogue`` first, then call
        this to retrieve a pack's actual content for context injection
        (guidance) or programmatic rule application (rules).

        name    The pack name from a catalogue entry (e.g.
                "standard-concept-preference").

        Returns the manifest fields plus:
          guidance   Markdown guidance text (guidance.md), or null if absent.
          rules      Parsed rules.yaml content, or null if absent.
          examples   Parsed examples.yaml content, or null if absent.

        Returns NOT_FOUND when no pack with that name exists.
        """
        try:
            content = catalogue.get_pack(name)
            if content is None:
                return {
                    "error": True,
                    "code": "NOT_FOUND",
                    "message": f"Knowledge pack {name!r} was not found",
                }
            payload = _manifest_payload(content.manifest)
            payload["guidance"] = content.guidance
            payload["rules"] = content.rules
            payload["examples"] = content.examples
            return payload
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    return True
