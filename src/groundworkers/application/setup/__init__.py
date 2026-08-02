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
from groundworkers.application.setup.embedding_artifacts import (
    check_artifact_compatibility,
    discover_embedding_artifacts,
)
from groundworkers.application.setup.embedding_coverage import (
    calculate_coverage,
    load_coverage,
)
from groundworkers.application.setup.embedding_setup import (
    OpenAICompatibleProviderAdapter,
    load_embedding_configuration,
    probe_embedding_store,
    probe_provider,
    reconcile_models,
)
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
)

__all__ = [
    "OpenAICompatibleProviderAdapter",
    "calculate_coverage",
    "check_artifact_compatibility",
    "classify_connection_error",
    "discover_embedding_artifacts",
    "load_configuration",
    "load_embedding_configuration",
    "load_graph_configuration",
    "load_llm_provider_configuration",
    "load_chat_configuration",
    "load_coverage",
    "probe_embedding_store",
    "probe_provider",
    "reconcile_models",
    "resolve_database_targets",
    "save_configuration",
    "verify_database_target",
]
