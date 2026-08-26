"""Background maintenance commands for non-graph performance indexes."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from oa_configurator import Resolver
from sqlalchemy import text

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.maintenance_runs import (
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceRunner,
    MaintenanceStep,
)
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
    MaintenanceCommand,
)
from groundworkers.base.sql import quote_identifier
from groundworkers.config import GroundworkersConfig


class PerformanceRemediation(StrEnum):
    TRIGRAM_INDEXES = "trigram_indexes"
    EMBEDDING_INDEX = "embedding_index"


def build_performance_commands(
    actions: Sequence[PerformanceRemediation],
    *,
    embedding_model: str | None = None,
    config_path: str | Path | None = None,
) -> tuple[MaintenanceCommand, ...]:
    """Build safe, out-of-process commands for the selected indexes."""

    environment = () if config_path is None else (("OA_CONFIG_PATH", str(Path(config_path).expanduser())),)
    commands: list[MaintenanceCommand] = []
    selected = set(actions)
    if PerformanceRemediation.TRIGRAM_INDEXES in selected:
        commands.append(
            MaintenanceCommand(
                argv=(
                    sys.executable,
                    "-m",
                    "groundworkers.application.setup.performance_maintenance",
                    "trigram",
                ),
                environment=environment,
            )
        )
    if PerformanceRemediation.EMBEDDING_INDEX in selected:
        if not embedding_model:
            raise ValueError("A registered embedding model is required to build its index.")
        commands.append(
            MaintenanceCommand(
                argv=(
                    shutil.which("omop-emb") or "omop-emb",
                    "maintenance",
                    "rebuild-index",
                    "--model",
                    embedding_model,
                    "--index-type",
                    "hnsw",
                    "--metric-type",
                    "cosine",
                ),
                environment=environment,
            )
        )
    return tuple(commands)


def start_performance_run(
    commands: Sequence[MaintenanceCommand],
    *,
    resource_key: str,
    runner: MaintenanceRunner | None = None,
) -> MaintenanceRun:
    return (runner or MaintenanceRunner()).start(
        MaintenancePlan(
            kind="performance-preparation",
            steps=tuple(
                MaintenanceStep(
                    key=f"performance-{index}",
                    command=command,
                    affected_resources=(resource_key,),
                )
                for index, command in enumerate(commands, start=1)
            ),
            affected_resources=(resource_key,),
        )
    )


def populate_trigram_indexes(snapshot: ConfigurationSnapshot) -> None:
    """Create the optional PostgreSQL trigram indexes Groundworkers can use."""

    if snapshot.stack is None:
        raise ValueError("A usable stack configuration is required.")
    config = GroundworkersConfig.validate_candidate(snapshot.stack)
    database = Resolver(snapshot.stack).resolve_database(config.cdm_db)
    engine = database.create_engine()
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("Trigram indexes require a PostgreSQL CDM database.")
        schema = getattr(database, "schema_name", None)
        prefix = f"{quote_identifier(engine, schema)}." if schema else ""
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            for table, column, index_name in (
                ("concept", "concept_name", "idx_concept_lower_name_trgm"),
                ("concept_synonym", "concept_synonym_name", "idx_concept_synonym_lower_name_trgm"),
            ):
                connection.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS {quote_identifier(connection, index_name)} "
                        f"ON {prefix}{quote_identifier(connection, table)} USING gin "
                        f"(lower({quote_identifier(connection, column)}) gin_trgm_ops)"
                    )
                )
    finally:
        engine.dispose()


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("trigram",))
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    snapshot = load_configuration(config_path=args.config)
    if not snapshot.usable:
        raise SystemExit("The selected stack configuration is not usable.")
    populate_trigram_indexes(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "PerformanceRemediation",
    "build_performance_commands",
    "populate_trigram_indexes",
    "start_performance_run",
]
