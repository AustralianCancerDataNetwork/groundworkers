"""Headless setup services for the Groundworkers operator console."""

from groundworkers.application.setup.configuration import (
    load_configuration,
    save_configuration,
)
from groundworkers.application.setup.databases import (
    classify_connection_error,
    resolve_database_targets,
    verify_database_target,
)
from groundworkers.application.setup.database_configuration import (
    apply_database_configuration,
    candidate_targets,
    draft_from_plan,
    plan_database_configuration,
)
from groundworkers.application.setup.embedding_artifacts import (
    check_artifact_compatibility,
    discover_embedding_artifacts,
)
from groundworkers.application.setup.embedding_coverage import (
    calculate_coverage,
    load_coverage,
)
from groundworkers.application.setup.embedding_population import (
    DEFAULT_EMBEDDING_BACKFILL_LIMIT,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    build_embedding_population_command,
    launch_embedding_population,
    load_embedding_coverage_report,
)
from groundworkers.application.setup.embedding_setup import (
    OpenAICompatibleProviderAdapter,
    load_embedding_configuration,
    probe_embedding_store,
    probe_provider,
    reconcile_models,
)
from groundworkers.application.setup.llm_configuration import (
    apply_llm_configuration,
    plan_llm_configuration,
    scan_llm_models,
)
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
    verify_llm_provider,
)

__all__ = [
    "OpenAICompatibleProviderAdapter",
    "apply_database_configuration",
    "apply_llm_configuration",
    "calculate_coverage",
    "candidate_targets",
    "check_artifact_compatibility",
    "classify_connection_error",
    "discover_embedding_artifacts",
    "draft_from_plan",
    "build_embedding_population_command",
    "DEFAULT_EMBEDDING_BACKFILL_LIMIT",
    "DEFAULT_EMBEDDING_BATCH_SIZE",
    "launch_embedding_population",
    "load_configuration",
    "load_embedding_coverage_report",
    "load_embedding_configuration",
    "load_graph_configuration",
    "load_llm_provider_configuration",
    "plan_database_configuration",
    "plan_llm_configuration",
    "load_chat_configuration",
    "load_coverage",
    "probe_embedding_store",
    "probe_provider",
    "reconcile_models",
    "resolve_database_targets",
    "save_configuration",
    "scan_llm_models",
    "verify_llm_provider",
    "verify_database_target",
]
