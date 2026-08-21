"""Stateless source-planning artifacts.

This package defines the neutral planning objects that dependency-facing
services can produce without knowing anything about ACP session state,
review queues, or persistence models.
"""

from groundworkers.services.source_planning.assisted import AssistedColumnRoleClassifier
from groundworkers.services.source_planning.classifier import (
    ColumnRoleClassifier,
    classify_columns,
    classify_tables,
)
from groundworkers.services.source_planning.decomposer import TableDecomposer
from groundworkers.services.source_planning.detector import FormatDetector
from groundworkers.services.source_planning.models import (
    COLUMN_ROLE_DESCRIPTIONS,
    UNCERTAIN_CONFIDENCE_THRESHOLD,
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
from groundworkers.services.source_planning.normalisation import (
    NormalisationPolicy,
    normalise_headers,
    normalise_table,
    normalise_tables,
)
from groundworkers.services.source_planning.provenance import HeaderProvenance
from groundworkers.services.source_planning.router import (
    IngesterRouter,
    route_table,
    route_tables,
)
from groundworkers.services.source_planning.service import (
    SourcePlanningService,
    plan_source,
    plan_tables,
)
from groundworkers.services.source_planning.warnings import (
    PlanningError,
    PlanningWarning,
)

__all__ = [
    "COLUMN_ROLE_DESCRIPTIONS",
    "UNCERTAIN_CONFIDENCE_THRESHOLD",
    "AnnotatedTable",
    "AssistedColumnRoleClassifier",
    "ColumnAnnotation",
    "ColumnRole",
    "ColumnRoleClassifier",
    "FormatDetector",
    "HeaderProvenance",
    "IngesterRouter",
    "IngestionPlan",
    "IngestionStrategy",
    "NormalisationPolicy",
    "NormalisedTable",
    "PlanningError",
    "PlanningWarning",
    "PreIngestBundle",
    "RawTable",
    "SourceFormat",
    "SourcePlanningService",
    "TableDecomposer",
    "classify_columns",
    "classify_tables",
    "normalise_headers",
    "normalise_table",
    "normalise_tables",
    "plan_source",
    "plan_tables",
    "route_table",
    "route_tables",
]
