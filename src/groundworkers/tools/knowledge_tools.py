"""MCP tools for the knowledge layer catalogue.

The knowledge_catalogue tool can be called at any workflow phase — source
planning, grounding, convention checking, review — to discover which
knowledge packs apply to a given job context. It is not coupled to
source_plan and intentionally lives as a standalone lookup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.knowledge.catalogue import KnowledgeCatalogue
from groundworkers.services.knowledge.models import KnowledgeLayer, PackManifest

logger = logging.getLogger(__name__)

# The bundled baseline packs are part of the package tree. Site-specific or
# localisation packs are added via configured roots on top of this baseline.
_PACKS_ROOT: Path = Path(__file__).resolve().parent.parent / "knowledge" / "packs"

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
    server: GroundworkersMCPServer,
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
        return json.dumps(
            {
                "packs": [_manifest_payload(m) for m in manifests],
                "total": len(manifests),
            }
        )

    @server.tool("knowledge_catalogue")
    def knowledge_catalogue(
        source_system: Annotated[
            str | None,
            Field(description="Detected source system (from source_plan), if any."),
        ] = None,
        domains: Annotated[
            list[str] | None,
            Field(
                description='OMOP domains involved in the job, for example ["Measurement", "Drug"].'
            ),
        ] = None,
        section_names: Annotated[
            list[str] | None,
            Field(
                description="Section names from the source, used to detect a specialty."
            ),
        ] = None,
        layer: Annotated[
            str | None,
            Field(
                description="Restrict to core, specialisation, source or localisation."
            ),
        ] = None,
        include_local: Annotated[
            bool, Field(description="Include local site packs (default true).")
        ] = True,
    ) -> dict[str, Any]:
        """Find the convention packs (OMOP and OHDSI conventions, plus local site rules) that apply
        to a job. Call it once at the start of any mapping or review job, with the OMOP domains
        involved, before choosing between candidates; then read each applicable pack with
        knowledge_pack. Returns name, scope_summary and see_also for each pack."""
        # Source planning supplies detected_source_system; section names drive specialty
        # fingerprint matching. Manifest results also retain layer, version, shareability,
        # mechanisms and applicability alongside scope_summary and see_also.
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
            resolved_layer = layer
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
        except Exception:
            raise

    @server.tool("knowledge_pack")
    def knowledge_pack(
        name: Annotated[
            str,
            Field(
                description="Pack name from knowledge_catalogue, for example standard-concept-preference."
            ),
        ],
    ) -> dict[str, Any]:
        """Read one convention pack by name (from knowledge_catalogue): its guidance text and
        rules, for example which concept level or vocabulary applies when candidates are equally
        precise. Apply its rules to the decision; they override general habit."""
        # The manifest is extended with guidance.md, rules.yaml and examples.yaml
        # content. Missing optional files yield null; unknown pack names yield NOT_FOUND.
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
        except Exception:
            raise

    return True
