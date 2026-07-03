"""MCP tools for the knowledge layer catalogue.

The knowledge_catalogue tool can be called at any workflow phase — source
planning, grounding, convention checking, review — to discover which
knowledge packs apply to a given job context. It is not coupled to
source_plan and intentionally lives as a standalone lookup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from groundworkers.base.server import GroundcrewServer
from groundworkers.services.knowledge.catalogue import KnowledgeCatalogue
from groundworkers.services.knowledge.models import PackManifest

logger = logging.getLogger(__name__)

# Packs live at groundworkers/knowledge/packs/ relative to the repo root.
# This path is dev-environment specific; production deployment will use
# package data (pyproject.toml include) or a configurable path.
# this is temporary pending oa-configurator upgrade to support a configurable knowledge packs root.
_PACKS_ROOT: Path = Path(__file__).parent.parent.parent.parent / "knowledge" / "packs"

_KNOWN_PACK_FILES = ("manifest.yaml", "rules.yaml", "guidance.md", "examples.yaml")


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


def register_knowledge_tools(
    server: GroundcrewServer,
    packs_root: Path | None = None,
) -> bool:
    """Register the knowledge catalogue tools if a packs root is available.

    Returns True when the tools were registered. When no configured packs root
    is given and the dev-only bundled path is absent (the common production
    case), the knowledge group is not registered at all — matching the
    documented rule that tool groups appear only when their backing store is
    available, rather than advertising a tool that returns an empty catalogue.
    """
    root = packs_root or _PACKS_ROOT
    if not root.exists():
        logger.info(
            "Knowledge packs root %s not found; knowledge_catalogue tools not registered.",
            root,
        )
        return False
    catalogue = KnowledgeCatalogue(root)

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
        try:
            results = catalogue.query(
                source_system=source_system,
                domains=domains,
                section_names=section_names,
                layer=layer,
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
