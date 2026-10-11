"""Background maintenance commands for non-graph performance indexes."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

import sqlalchemy as sa
from groundskeeping.contracts import Command, CommandPlan, CommandStep
from oa_configurator import (
    ResolvedCDMDatabase,
    Resolver,
    Role,
    physical_schema_of,
    qualified,
)
from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Synonym
from omop_alchemy.config import create_cdm_engines
from sqlalchemy import Column, Index, MetaData, String, Table, func
from sqlalchemy.engine import Connection

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.databases import (
    GROUNDWORKERS_NORMALIZED_INDEX_TARGETS,
    normalized_index_matches,
)
from groundworkers.application.setup.maintenance_runs import (
    MaintenanceRun,
    MaintenanceRunner,
)
from groundworkers.application.setup.models import (
    ConfigurationSnapshot,
)
from groundworkers.base.sql import (
    normalization_key_expression,
)
from groundworkers.config import GroundworkersConfig

_MODULE_NAME = (
    __spec__.name
    if __spec__ is not None
    else "groundworkers.application.setup.performance_maintenance"
)


class PerformanceRemediation(StrEnum):
    TRIGRAM_INDEXES = "trigram_indexes"
    NORMALIZED_INDEXES = "normalized_indexes"
    EMBEDDING_INDEX = "embedding_index"


def build_performance_commands(
    actions: Sequence[PerformanceRemediation],
    *,
    embedding_model: str | None = None,
    config_path: str | Path | None = None,
) -> tuple[Command, ...]:
    """Build safe, out-of-process commands for the selected indexes."""

    environment = (
        ()
        if config_path is None
        else (("OA_CONFIG_PATH", str(Path(config_path).expanduser())),)
    )
    commands: list[Command] = []
    selected = set(actions)
    for remediation, action in (
        (PerformanceRemediation.TRIGRAM_INDEXES, "trigram"),
        (PerformanceRemediation.NORMALIZED_INDEXES, "normalized"),
    ):
        if remediation in selected:
            commands.append(
                Command(
                    argv=(sys.executable, "-m", _MODULE_NAME, action),
                    environment=environment,
                )
            )
    if PerformanceRemediation.EMBEDDING_INDEX in selected:
        if not embedding_model:
            raise ValueError(
                "A registered embedding model is required to build its index."
            )
        commands.append(
            Command(
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
    commands: Sequence[Command],
    *,
    resource_key: str,
    runner: MaintenanceRunner | None = None,
) -> MaintenanceRun:
    return (runner or MaintenanceRunner()).start(
        CommandPlan(
            kind="performance-preparation",
            steps=tuple(
                CommandStep(
                    key=f"performance-{index}",
                    command=command,
                    affected_resources=(resource_key,),
                )
                for index, command in enumerate(commands, start=1)
            ),
            affected_resources=(resource_key,),
        )
    )


@contextmanager
def _postgres_cdm(
    snapshot: ConfigurationSnapshot, *, purpose: str
) -> Iterator[tuple[Connection, str | None]]:
    """Yield a transaction on the PostgreSQL vocabulary role and its schema."""

    if snapshot.stack is None:
        raise ValueError("A usable stack configuration is required.")
    config = GroundworkersConfig.validate_candidate(snapshot.stack)
    database = Resolver(snapshot.stack).resolve_database(config.cdm_db)
    if not isinstance(database, ResolvedCDMDatabase):
        raise TypeError("Groundworkers cdm_db must resolve to a CDM database.")
    primary_engine, vocab_engine = create_cdm_engines(
        database, register_claims=False
    )
    try:
        if vocab_engine.dialect.name != "postgresql":
            raise ValueError(f"{purpose} require a PostgreSQL CDM database.")
        schema = database.schema_for_role(Role.VOCAB)
        with vocab_engine.begin() as connection:
            yield connection, schema
    finally:
        primary_engine.dispose()
        if vocab_engine is not primary_engine:
            vocab_engine.dispose()


def populate_trigram_indexes(snapshot: ConfigurationSnapshot) -> None:
    """Create the optional PostgreSQL trigram indexes Groundworkers can use."""

    with _postgres_cdm(snapshot, purpose="Trigram indexes") as (connection, _schema):
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for table, column, index_name in (
            ("concept", "concept_name", "idx_concept_lower_name_trgm"),
            (
                "concept_synonym",
                "concept_synonym_name",
                "idx_concept_synonym_lower_name_trgm",
            ),
        ):
            light_table = Table(
                table,
                MetaData(),
                Column(column, String),
                schema=Role.VOCAB.value,
            )
            index = Index(
                index_name,
                func.lower(light_table.c[column]).label("lower_name"),
                postgresql_using="gin",
                postgresql_ops={"lower_name": "gin_trgm_ops"},
            )
            connection.execute(sa.schema.CreateIndex(index, if_not_exists=True))


def populate_normalized_indexes(snapshot: ConfigurationSnapshot) -> None:
    """Create the expression indexes that serve ``search_normalized``.

    The indexed expression is rendered from the query's own builder, so the
    index matches on any PostgreSQL setup regardless of plan caching. An index
    of the same name built from an older expression is replaced, not kept.
    """

    model_tables = {"concept": Concept, "concept_synonym": Concept_Synonym}
    with _postgres_cdm(snapshot, purpose="Normalized-name indexes") as (
        connection,
        schema,
    ):
        for table, column, _label, index_name in GROUNDWORKERS_NORMALIZED_INDEX_TARGETS:
            model_table = model_tables[table]
            light_table = sa.Table(
                table,
                sa.MetaData(),
                sa.Column(column, sa.String),
                schema=model_table.__table__.schema,
            )
            old_name = (
                "idx_concept_name_normalized"
                if table == "concept"
                else "idx_concept_synonym_name_normalized"
            )
            connection.execute(
                sa.schema.DropIndex(
                    sa.Index(old_name, light_table.c[column]),
                    if_exists=True,
                )
            )
            if normalized_index_matches(connection, schema, table, column):
                continue
            index = sa.Index(
                index_name,
                normalization_key_expression(light_table.c[column]),
            )
            connection.execute(
                sa.schema.DropIndex(index, if_exists=True)
            )
            connection.execute(sa.schema.CreateIndex(index))
            table_name = qualified(
                connection,
                table,
                physical_schema=physical_schema_of(
                    connection, schema_tag=Role.VOCAB
                ),
            )
            connection.exec_driver_sql(f"ANALYZE {table_name}")


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("trigram", "normalized"))
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    snapshot = load_configuration(config_path=args.config)
    if not snapshot.usable:
        raise SystemExit("The selected stack configuration is not usable.")
    if args.action == "normalized":
        populate_normalized_indexes(snapshot)
    else:
        populate_trigram_indexes(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "PerformanceRemediation",
    "build_performance_commands",
    "populate_normalized_indexes",
    "populate_trigram_indexes",
    "start_performance_run",
]
