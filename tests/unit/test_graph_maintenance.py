from __future__ import annotations

from pathlib import Path

import pytest

from groundworkers.application.setup.graph_maintenance import (
    GraphRemediation,
    GraphRemediationRequest,
    build_graph_remediation_commands,
    outstanding_remediations,
)
from groundworkers.application.setup.models import (
    ConnectionResult,
    DiagnosticSeverity,
    ResourceDiagnostic,
)


def _readiness(*diagnostics: ResourceDiagnostic) -> ConnectionResult:
    return ConnectionResult(
        target_key="database.graph",
        connected=True,
        latency_ms=1.0,
        safe_url="postgresql://host/db",
        diagnostics=diagnostics,
    )


def _warn(code: str) -> ResourceDiagnostic:
    return ResourceDiagnostic(code, "missing", DiagnosticSeverity.WARNING)


def _info(code: str) -> ResourceDiagnostic:
    return ResourceDiagnostic(code, "present", DiagnosticSeverity.INFO)


# ---------------------------------------------------------------------------
# Deciding what is outstanding
# ---------------------------------------------------------------------------


def test_outstanding_is_read_from_the_readiness_diagnostics() -> None:
    readiness = _readiness(
        _warn("graph_tables_missing"),
        _warn("fulltext_indexes_missing"),
        _info("functional_indexes_present"),
    )

    assert outstanding_remediations(readiness) == (
        GraphRemediation.RELATIONSHIP_CLASSIFICATION,
        GraphRemediation.FULLTEXT_INDEXES,
    )


def test_empty_relationship_tables_count_as_outstanding() -> None:
    """Tables present but empty is the same problem as absent, for grounding."""
    readiness = _readiness(
        _info("graph_tables_present"), _warn("graph_tables_empty")
    )

    assert outstanding_remediations(readiness) == (
        GraphRemediation.RELATIONSHIP_CLASSIFICATION,
    )


def test_a_fully_ready_graph_has_nothing_outstanding() -> None:
    readiness = _readiness(
        _info("graph_tables_present"),
        _info("fulltext_indexes_present"),
        _info("functional_indexes_present"),
    )

    assert outstanding_remediations(readiness) == ()


def test_an_unrun_check_claims_nothing() -> None:
    assert outstanding_remediations(None) == ()


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_functional_indexes_include_the_vocabulary_tables() -> None:
    commands = build_graph_remediation_commands(
        GraphRemediationRequest(actions=(GraphRemediation.FUNCTIONAL_INDEXES,))
    )

    assert len(commands) == 1
    argv = commands[0].argv
    assert argv[1:] == ("indexes", "enable", "--vocab")


def test_declining_cluster_is_passed_through() -> None:
    """CLUSTER rewrites the heap, so the operator's choice must reach the CLI."""
    commands = build_graph_remediation_commands(
        GraphRemediationRequest(
            actions=(GraphRemediation.FUNCTIONAL_INDEXES,), cluster=False
        )
    )

    assert "--no-cluster" in commands[0].argv


def test_fulltext_installs_then_populates() -> None:
    """Installing alone leaves the indexes empty and full-text matching nothing."""
    commands = build_graph_remediation_commands(
        GraphRemediationRequest(actions=(GraphRemediation.FULLTEXT_INDEXES,))
    )

    assert [command.argv[1:] for command in commands] == [
        ("fulltext", "install"),
        ("fulltext", "populate"),
    ]


def test_relationship_classification_passes_the_csv_directory(tmp_path: Path) -> None:
    commands = build_graph_remediation_commands(
        GraphRemediationRequest(
            actions=(GraphRemediation.RELATIONSHIP_CLASSIFICATION,),
            predicate_csv_dir=tmp_path,
        )
    )

    assert commands[0].argv[1:] == (
        "relationship-classification",
        "--pred-class-dir",
        str(tmp_path),
    )


def test_relationship_classification_without_a_directory_is_refused() -> None:
    """omop-graph loads these from CSV and ships none, so there is no default."""
    with pytest.raises(ValueError, match=r"predicate_classification\.csv"):
        build_graph_remediation_commands(
            GraphRemediationRequest(
                actions=(GraphRemediation.RELATIONSHIP_CLASSIFICATION,)
            )
        )


def test_commands_are_ordered_tables_then_indexes(tmp_path: Path) -> None:
    """Indexing before the rows exist would index nothing."""
    commands = build_graph_remediation_commands(
        GraphRemediationRequest(
            actions=tuple(GraphRemediation), predicate_csv_dir=tmp_path
        )
    )

    steps = [command.argv[1:3] for command in commands]
    assert steps[0][0] == "relationship-classification"
    assert steps[-1] == ("indexes", "enable")


def test_the_config_path_is_carried_into_the_subprocess(tmp_path: Path) -> None:
    """The CLIs resolve their own config, so they must see the same file."""
    config = tmp_path / "config.toml"

    commands = build_graph_remediation_commands(
        GraphRemediationRequest(actions=(GraphRemediation.FUNCTIONAL_INDEXES,)),
        config_path=config,
    )

    assert ("OA_CONFIG_PATH", str(config)) in commands[0].environment


def test_selecting_nothing_builds_nothing() -> None:
    assert build_graph_remediation_commands(GraphRemediationRequest(actions=())) == ()


# ---------------------------------------------------------------------------
# Bundled predicate CSVs (temporary, pending the omop-graph packaging fix)
# ---------------------------------------------------------------------------


def test_the_bundled_predicate_csvs_are_installed() -> None:
    """They are a stand-in for data omop-graph does not ship in its wheel.

    Guards the same failure this works around: files outside a package directory
    vanish from the wheel silently, with nothing failing until runtime.
    """
    from groundworkers.application.setup.graph_maintenance import (
        PREDICATE_CSV_NAMES,
        packaged_predicate_csv_dir,
    )

    bundled = packaged_predicate_csv_dir()

    assert bundled is not None
    for name in PREDICATE_CSV_NAMES:
        assert (bundled / name).is_file()
        assert (bundled / name).stat().st_size > 0
