"""Stateless source-planning artifacts for OMOP-grounding-adjacent ingestion.

This package defines the neutral planning objects that dependency-facing
services can produce without knowing anything about ACP session state,
review queues, or persistence models.
"""

from groundworkers.source_planning.models import (
    AnnotatedTable,
    ColumnAnnotation,
    ColumnRole,
    IngestionPlan,
    IngestionStrategy,
    NormalisedTable,
    PreIngestBundle,
    RawTable,
    SourceFormat,
)
from groundworkers.source_planning.normalisation import normalise_headers
from groundworkers.source_planning.provenance import HeaderProvenance
from groundworkers.source_planning.warnings import PlanningError, PlanningWarning

__all__ = [
    "AnnotatedTable",
    "ColumnAnnotation",
    "ColumnRole",
    "HeaderProvenance",
    "IngestionPlan",
    "IngestionStrategy",
    "NormalisedTable",
    "PlanningError",
    "PlanningWarning",
    "PreIngestBundle",
    "RawTable",
    "SourceFormat",
    "normalise_headers",
]
