"""Remediations for the gaps the graph readiness check reports.

Each one is a sibling package's own CLI command, run out-of-process. Groundworkers
neither reimplements the DDL nor imports another package's CLI internals; it
decides *which* remediation is needed from the readiness diagnostics it already
collects, and builds the command line.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from groundskeeping.contracts import Command, CommandPlan, CommandStep
from oa_configurator import ResolvedCDMDatabase, Resolver, StackConfig

from groundworkers.application.setup.maintenance_runs import (
    MaintenanceRun,
    MaintenanceRunner,
)
from groundworkers.application.setup.models import (
    ConnectionResult,
    DiagnosticSeverity,
)
from groundworkers.base.database import validate_duckdb_cdm_paths
from groundworkers.config import GroundworkersConfig

__all__ = [
    "PREDICATE_CSV_NAMES",
    "GraphRemediation",
    "GraphRemediationRequest",
    "build_fulltext_commands",
    "build_graph_remediation_commands",
    "cdm_is_duckdb",
    "outstanding_remediations",
    "start_graph_remediation_run",
    "validate_graph_preparation_paths",
]

# The two files required when an operator chooses a custom classification directory.
PREDICATE_CSV_NAMES = ("predicate_classification.csv", "predicate_mapping.csv")


class GraphRemediation(StrEnum):
    """A gap the readiness check can report, and the command that closes it."""

    RELATIONSHIP_CLASSIFICATION = "relationship_classification"
    FULLTEXT_INDEXES = "fulltext_indexes"
    FUNCTIONAL_INDEXES = "functional_indexes"


# Readiness diagnostic codes that indicate each gap. The check emits an INFO
# "present" code when satisfied, so anything at WARNING or above is outstanding.
_REMEDIATION_CODES: dict[GraphRemediation, tuple[str, ...]] = {
    GraphRemediation.RELATIONSHIP_CLASSIFICATION: (
        "graph_tables_missing",
        "graph_tables_empty",
    ),
    GraphRemediation.FULLTEXT_INDEXES: (
        "fulltext_indexes_missing",
        "fulltext_sidecar_unpopulated",
    ),
    GraphRemediation.FUNCTIONAL_INDEXES: ("functional_indexes_missing",),
}


@dataclass(frozen=True)
class GraphRemediationRequest:
    actions: tuple[GraphRemediation, ...]
    predicate_csv_dir: Path | None = None
    cluster: bool = True
    duckdb: bool = False


def build_fulltext_commands(
    *,
    config_path: str | Path | None = None,
) -> tuple[Command, ...]:
    """Build the complete full-text preparation sequence.

    Installing the sidecars and GIN indexes is not enough: the sidecar values
    must also be populated before full-text search can return candidates.
    """

    environment: tuple[tuple[str, str], ...] = ()
    if config_path is not None:
        environment = (("OA_CONFIG_PATH", str(Path(config_path).expanduser())),)
    executable = _executable("omop-alchemy")
    return tuple(
        Command(
            argv=(executable, "fulltext", action),
            environment=environment,
        )
        for action in ("install", "populate")
    )


def cdm_is_duckdb(stack: StackConfig | None) -> bool:
    """Detect DuckDB so setup can omit PostgreSQL-specific maintenance."""
    if stack is None:
        return False
    try:
        config = GroundworkersConfig.validate_candidate(stack)
        database = Resolver(stack).resolve_database(config.cdm_db)
    except (ValueError, TypeError, KeyError):
        return False
    return isinstance(database, ResolvedCDMDatabase) and any(
        connection.dialect_name == "duckdb"
        for connection in (database.connection, database.vocab_connection)
    )


def validate_graph_preparation_paths(stack: StackConfig | None) -> None:
    """Validate CDM filenames before an upstream maintenance CLI can open a writer."""
    if stack is None:
        raise ValueError("Configure the CDM database before preparing the graph.")
    config = GroundworkersConfig.validate_candidate(stack)
    cdm = Resolver(stack).resolve_database(config.cdm_db)
    if isinstance(cdm, ResolvedCDMDatabase):
        validate_duckdb_cdm_paths(cdm)


def outstanding_remediations(
    readiness: ConnectionResult | None,
) -> tuple[GraphRemediation, ...]:
    """Which remediations the readiness diagnostics say are still needed.

    Used to pre-select the wizard's checkboxes, so the operator confirms what the
    check already found rather than rediscovering it.
    """
    if readiness is None:
        return ()
    unmet = {
        diagnostic.code
        for diagnostic in readiness.diagnostics
        if diagnostic.severity is not DiagnosticSeverity.INFO
    }
    return tuple(
        remediation
        for remediation, codes in _REMEDIATION_CODES.items()
        if unmet.intersection(codes)
    )


def build_graph_remediation_commands(
    request: GraphRemediationRequest,
    *,
    config_path: str | Path | None = None,
) -> tuple[Command, ...]:
    """Build the CLI commands for the selected remediations, in dependency order.

    The upstream packaged classification is used unless a directory is supplied.
    """
    if request.duckdb and any(
        action is not GraphRemediation.RELATIONSHIP_CLASSIFICATION
        for action in request.actions
    ):
        raise ValueError(
            "Full-text and functional index preparation requires PostgreSQL. "
            "DuckDB supports relationship classification only."
        )
    environment: tuple[tuple[str, str], ...] = ()
    if config_path is not None:
        environment = (("OA_CONFIG_PATH", str(Path(config_path).expanduser())),)

    commands: list[Command] = []
    selected = set(request.actions)

    if GraphRemediation.RELATIONSHIP_CLASSIFICATION in selected:
        argv = [_executable("omop-graph"), "relationship-classification"]
        if request.predicate_csv_dir is not None:
            argv.extend(("--pred-class-dir", str(request.predicate_csv_dir)))
        commands.append(
            Command(
                argv=tuple(argv),
                environment=environment,
            )
        )

    if GraphRemediation.FULLTEXT_INDEXES in selected:
        commands.extend(build_fulltext_commands(config_path=config_path))

    if GraphRemediation.FUNCTIONAL_INDEXES in selected:
        argv = [_executable("omop-alchemy"), "indexes", "enable", "--vocab"]
        if not request.cluster:
            # CLUSTER rewrites the whole heap; on large vocabulary tables that
            # needs disk headroom the operator may not have.
            argv.append("--no-cluster")
        commands.append(Command(argv=tuple(argv), environment=environment))

    return tuple(commands)


def start_graph_remediation_run(
    commands: tuple[Command, ...],
    *,
    resource_key: str = "graph:default-cdm",
    runner: MaintenanceRunner | None = None,
) -> MaintenanceRun:
    """Persist and start an ordered graph-preparation maintenance run."""

    plan = CommandPlan(
        kind="graph-preparation",
        steps=tuple(
            CommandStep(
                key=f"graph-{index}",
                command=command,
                affected_resources=(resource_key,),
            )
            for index, command in enumerate(commands, start=1)
        ),
        affected_resources=(resource_key,),
    )
    return (runner or MaintenanceRunner()).start(plan)


def _executable(name: str) -> str:
    beside_python = Path(sys.executable).with_name(name)
    return (
        str(beside_python) if beside_python.is_file() else (shutil.which(name) or name)
    )
