"""Remediations for the gaps the graph readiness check reports.

Each one is a sibling package's own CLI command, run out-of-process. Groundworkers
neither reimplements the DDL nor imports another package's CLI internals; it
decides *which* remediation is needed from the readiness diagnostics it already
collects, and builds the command line.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

from groundworkers.application.setup.maintenance import (
    MaintenanceCommandError,
    run_maintenance_command,
)
from groundworkers.application.setup.maintenance_runs import (
    MaintenancePlan,
    MaintenanceRun,
    MaintenanceRunner,
    MaintenanceStep,
)
from groundworkers.application.setup.models import (
    ConnectionResult,
    DiagnosticSeverity,
    MaintenanceCommand,
    MaintenanceLaunch,
)

__all__ = [
    "PREDICATE_CSV_NAMES",
    "GraphRemediation",
    "GraphRemediationRequest",
    "build_graph_remediation_commands",
    "launch_graph_remediation",
    "outstanding_remediations",
    "packaged_predicate_csv_dir",
    "start_graph_remediation_run",
]

# The two files omop-graph's loader reads. Named here because both the packaged
# lookup and the wizard's validation need the same list.
PREDICATE_CSV_NAMES = ("predicate_classification.csv", "predicate_mapping.csv")


def packaged_predicate_csv_dir() -> Path | None:
    """The predicate CSVs bundled with Groundworkers, if they are present.

    TEMPORARY. These are a verbatim copy of omop-graph's own `config/` files,
    which live outside its packaged directory and so never reach its wheel --
    leaving `omop-graph relationship-classification` unrunnable from a normal
    install. An upstream issue is open to ship them; delete this function, the
    `config/` directory, and the wizard's default once it lands.

    Returns ``None`` rather than a missing path so the caller can fall back to
    asking, instead of offering a default that does not exist.
    """
    bundled = Path(str(resources.files("groundworkers") / "config"))
    if all((bundled / name).is_file() for name in PREDICATE_CSV_NAMES):
        return bundled
    return None


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
    GraphRemediation.FULLTEXT_INDEXES: ("fulltext_indexes_missing",),
    GraphRemediation.FUNCTIONAL_INDEXES: ("functional_indexes_missing",),
}


@dataclass(frozen=True)
class GraphRemediationRequest:
    actions: tuple[GraphRemediation, ...]
    predicate_csv_dir: Path | None = None
    cluster: bool = True


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
) -> tuple[MaintenanceCommand, ...]:
    """Build the CLI commands for the selected remediations, in dependency order.

    Raises
    ------
    ValueError
        If relationship classification is selected without a source directory,
        which omop-graph requires and does not ship.
    """
    environment: tuple[tuple[str, str], ...] = ()
    if config_path is not None:
        environment = (("OA_CONFIG_PATH", str(Path(config_path).expanduser())),)

    commands: list[MaintenanceCommand] = []
    selected = set(request.actions)

    if GraphRemediation.RELATIONSHIP_CLASSIFICATION in selected:
        if request.predicate_csv_dir is None:
            raise ValueError(
                "Relationship classification needs a directory holding "
                "predicate_classification.csv and predicate_mapping.csv."
            )
        commands.append(
            MaintenanceCommand(
                argv=(
                    _executable("omop-graph"),
                    "relationship-classification",
                    "--pred-class-dir",
                    str(request.predicate_csv_dir),
                ),
                environment=environment,
            )
        )

    if GraphRemediation.FULLTEXT_INDEXES in selected:
        # install creates the tsvector columns and GIN indexes; populate fills
        # them. Installing alone leaves the indexes empty, so full-text grounding
        # would match nothing while the readiness check reported them present.
        commands.append(
            MaintenanceCommand(
                argv=(_executable("omop-alchemy"), "fulltext", "install"),
                environment=environment,
            )
        )
        commands.append(
            MaintenanceCommand(
                argv=(_executable("omop-alchemy"), "fulltext", "populate"),
                environment=environment,
            )
        )

    if GraphRemediation.FUNCTIONAL_INDEXES in selected:
        argv = [_executable("omop-alchemy"), "indexes", "enable", "--vocab"]
        if not request.cluster:
            # CLUSTER rewrites the whole heap; on large vocabulary tables that
            # needs disk headroom the operator may not have.
            argv.append("--no-cluster")
        commands.append(
            MaintenanceCommand(argv=tuple(argv), environment=environment)
        )

    return tuple(commands)


def launch_graph_remediation(
    commands: Sequence[MaintenanceCommand],
    *,
    log_dir: str | Path = "/tmp",
) -> tuple[MaintenanceLaunch, ...]:
    """Run graph prerequisites sequentially and stop at the first failure."""

    launches: list[MaintenanceLaunch] = []
    for index, command in enumerate(commands, start=1):
        try:
            launches.append(
                run_maintenance_command(
                    command,
                    log_prefix=f"graph-{index}",
                    log_dir=log_dir,
                )
            )
        except MaintenanceCommandError as exc:
            completed = ", ".join(str(item.pid) for item in launches) or "none"
            raise RuntimeError(
                f"Graph preparation stopped after step {index} failed; "
                f"completed PIDs: {completed}. Failed log: {exc.launch.log_path}"
            ) from exc
    return tuple(launches)


def start_graph_remediation_run(
    commands: Sequence[MaintenanceCommand],
    *,
    resource_key: str = "graph:default-cdm",
    runner: MaintenanceRunner | None = None,
) -> MaintenanceRun:
    """Persist and start an ordered graph-preparation maintenance run."""

    plan = MaintenancePlan(
        kind="graph-preparation",
        steps=tuple(
            MaintenanceStep(
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
    return shutil.which(name) or name
