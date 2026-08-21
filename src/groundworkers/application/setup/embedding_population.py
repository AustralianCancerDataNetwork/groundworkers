from __future__ import annotations

import re
import shutil
from importlib import import_module
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from oa_configurator import Resolver, safe_endpoint
from omop_llm import canonical_model_name
from sqlalchemy.engine import Engine

from groundworkers.application.setup.embedding_coverage import calculate_coverage
from groundworkers.application.setup.embedding_setup import load_embedding_configuration
from groundworkers.application.setup.maintenance import launch_maintenance_command
from groundworkers.application.setup.maintenance_runs import (
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceRunner,
    MaintenanceStep,
)
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    CoverageScope,
    CoverageSnapshot,
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
    EmbeddingIndexSnapshot,
    EmbeddingPopulationCommand,
    EmbeddingPopulationLaunch,
    EmbeddingPopulationRequest,
)
from groundworkers.base.results import enum_value
from groundworkers.config import GroundworkersConfig

_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)

DEFAULT_EMBEDDING_BATCH_SIZE = 100
DEFAULT_EMBEDDING_BACKFILL_LIMIT = 1000


def load_embedding_coverage_report(
    snapshot: ConfigurationSnapshot,
    *,
    standard_only: bool = True,
) -> EmbeddingCoverageReport | None:
    """Inspect CDM/vector coverage for the configured embedding model."""

    configuration = load_embedding_configuration(snapshot)
    if configuration is None or snapshot.stack is None:
        return None
    scope = CoverageScope(
        model_name=configuration.model_name,
        metric="cosine",
        vocabularies=(),
        standard_only=standard_only,
        valid_only=False,
    )
    store = None
    try:
        groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
        if (
            groundworkers.embedding_model_name is None
            or groundworkers.vector_store_name is None
        ):
            raise ValueError("Embedding model and vector store references are required.")
        resolver = Resolver(snapshot.stack)
        resolved_model = resolver.resolve_model(groundworkers.embedding_model_name)
        vector_store = resolver.resolve_vector_store(
            groundworkers.vector_store_name
        )
        store = inspect_resolved_vector_store(vector_store)
        cdm_engine, _ = _cdm_engine_and_schema(snapshot)
        try:
            canonical_model = _canonical_model_name(
                resolved_model.model,
                provider_kind=resolved_model.provider.provider,
            )
            plan = plan_population(
                cdm_engine,
                store,
                model_name=canonical_model,
                scope=_population_scope(
                    standard_only=standard_only,
                    valid_only=False,
                ),
            )
        finally:
            cdm_engine.dispose()
        index = _embedding_index_snapshot(
            store=store,
            canonical_model=canonical_model,
        )
        coverage_scope = CoverageScope(
            model_name=canonical_model,
            metric=index.registry_metric or "cosine",
            vocabularies=tuple(row.vocabulary for row in plan.rows),
            standard_only=standard_only,
            valid_only=False,
        )
        coverage = calculate_coverage(
            coverage_scope,
            eligible={row.vocabulary: len(row.eligible_ids) for row in plan.rows},
            embedded={row.vocabulary: len(row.compatible_ids) for row in plan.rows},
        )
        coverage = replace_coverage_metadata(
            coverage,
            plan=plan,
        )
    except Exception as exc:
        # Broad except: rendered as secret-safe setup state.
        coverage = CoverageSnapshot(
            scope=scope,
            available=False,
            blocker=_coverage_blocker(exc),
        )
        index = EmbeddingIndexSnapshot(
            model_name=configuration.model_name,
            registered=False,
        )
    finally:
        if store is not None:
            store.close()
    return EmbeddingCoverageReport(
        configuration=configuration,
        coverage=coverage,
        index=index,
    )


def _coverage_blocker(exc: Exception) -> str:
    """Say why coverage failed, without handing a driver's DSN to the screen.

    ``ValueError`` here is a configuration verdict: the required references are
    missing, or a provider has refused the model name it was given. Those
    messages are authored for an operator and are the whole point of the
    failure, so withholding them -- as this used to, naming only the exception
    class -- turned an actionable answer into a dead end.

    Every other exception is an operational failure raised by a driver or an
    engine, and those routinely quote the connection string that failed. They
    keep the class-name-only form. URLs are scrubbed from whatever is shown, so
    a message that does carry one cannot leak a credential either way.
    """
    if isinstance(exc, ValueError):
        detail = _scrub_urls(str(exc).strip())
        if detail:
            return f"Embedding coverage could not be loaded. {detail}"
    return (
        "Embedding coverage could not be loaded because setup failed with "
        f"{type(exc).__name__}."
    )


def _scrub_urls(text: str) -> str:
    """Mask credentials in any URL the text carries.

    Delegates the definition of a safe URL to ``oa_configurator.safe_endpoint``,
    the same primitive its logging filter uses. Only the regex is local, because
    oa-configurator keeps its copy private to the logging module; see the
    upstream ticket to have it export the scrub itself.
    """
    return _URL_RE.sub(lambda match: safe_endpoint(match.group(0)) or "***", text)


def build_embedding_population_command(
    configuration: EmbeddingConfiguration,
    request: EmbeddingPopulationRequest,
    *,
    config_path: str | Path | None = None,
) -> EmbeddingPopulationCommand:
    """Build the omop-emb command used to populate missing concept embeddings."""

    executable = shutil.which("omop-emb") or "omop-emb"
    argv: list[str] = [
        executable,
        "embeddings",
        "add-embeddings",
        "--model-name",
        configuration.model_entry_name,
        "--batch-size",
        str(request.batch_size),
    ]
    if request.standard_only:
        argv.append("--standard-only")
    for vocabulary in request.vocabularies:
        argv.extend(("--vocabulary", vocabulary))
    if request.limit is not None:
        argv.extend(("--num-embeddings", str(request.limit)))
    environment: list[tuple[str, str]] = []
    if config_path is not None:
        environment.append(("OA_CONFIG_PATH", str(Path(config_path).expanduser())))
    return EmbeddingPopulationCommand(
        argv=tuple(argv),
        environment=tuple(environment),
    )


def launch_embedding_population(
    command: EmbeddingPopulationCommand,
    *,
    log_dir: str | Path = "/tmp",
) -> EmbeddingPopulationLaunch:
    """Start an omop-emb population command and return immediately."""

    return launch_maintenance_command(
        command, log_prefix="omop-emb", log_dir=log_dir
    )


def start_embedding_population_run(
    command: EmbeddingPopulationCommand,
    *,
    resource_key: str,
    runner: MaintenanceRunner | None = None,
) -> MaintenanceRun:
    """Persist and start an embedding population run for one model/store."""

    plan = MaintenancePlan(
        kind="embedding-population",
        steps=(
            MaintenanceStep(
                key="populate-embeddings",
                command=command,
                affected_resources=(resource_key,),
            ),
        ),
        affected_resources=(resource_key,),
    )
    return (runner or MaintenanceRunner()).start(plan)


def replace_coverage_metadata(coverage: CoverageSnapshot, *, plan) -> CoverageSnapshot:
    """Attach identity comparison evidence to the operator-facing snapshot."""

    return replace(
        coverage,
        metadata={
            **coverage.metadata,
            "identity_aware": True,
            "store_initialized": plan.store_initialized,
            "stale_total": len(plan.stale_ids),
            "metadata_changed_total": len(plan.metadata_changed_ids),
            "changed_source_text_total": len(plan.changed_source_text_ids),
            "source_text_provenance": plan.source_text_provenance,
        },
    )


def _cdm_engine_and_schema(snapshot: ConfigurationSnapshot) -> tuple[Engine, str]:
    assert snapshot.stack is not None
    resolver = Resolver(snapshot.stack)
    groundworkers = GroundworkersConfig.validate_candidate(snapshot.stack)
    database = resolver.resolve_database(groundworkers.cdm_db)
    return database.create_engine(), database.schema_name or "main"


def _embedding_index_snapshot(
    *,
    store,
    canonical_model: str,
) -> EmbeddingIndexSnapshot:
    record = store.model(canonical_model)
    if record is None:
        return EmbeddingIndexSnapshot(model_name=canonical_model, registered=False)
    physical_indexes = store.physical_indexes(canonical_model)
    return EmbeddingIndexSnapshot(
        model_name=canonical_model,
        registered=True,
        storage_identifier=record.storage_identifier,
        registry_index_type=enum_value(record.index_type),
        registry_metric=enum_value(record.metric_type),
        physical_indexes=physical_indexes,
        drop_sql=store.drop_index_sql(canonical_model),
    )


def _canonical_model_name(model_name: str, *, provider_kind: str) -> str:
    return canonical_model_name(provider_kind, model_name)


def inspect_resolved_vector_store(resolved):
    """Load the optional read-only omop-emb contract at use time."""

    from omop_emb.backends import inspect_resolved_vector_store as inspect_store

    return inspect_store(resolved)


def plan_population(cdm_engine, store, *, model_name: str, scope):
    """Load the optional identity-planning contract at use time."""

    population = cast(Any, import_module("omop_emb.population"))

    return population.plan_population(
        cdm_engine,
        store,
        model_name=model_name,
        scope=scope,
    )


def _population_scope(**kwargs):
    population = cast(Any, import_module("omop_emb.population"))

    return population.PopulationScope(**kwargs)
