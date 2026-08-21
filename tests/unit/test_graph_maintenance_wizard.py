from __future__ import annotations

from pathlib import Path

from groundskeeping.contracts.wizards import ReviewStep, WizardResultStatus
from oa_configurator import save_stack_config

from groundworkers.application.setup.models import (
    ConnectionResult,
    DiagnosticSeverity,
    MaintenanceCommand,
    MaintenanceLaunch,
    ResourceDiagnostic,
)
from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.graph_maintenance import (
    GraphMaintenanceWizardController,
)
from tests.support.stack_config import build_cdm_stack


def _session(tmp_path: Path) -> SetupSession:
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)
    return SetupSession(config_path=path)


def _readiness(*codes: str) -> ConnectionResult:
    return ConnectionResult(
        target_key="database.graph",
        connected=True,
        latency_ms=1.0,
        safe_url="postgresql://host/db",
        diagnostics=tuple(
            ResourceDiagnostic(code, "missing", DiagnosticSeverity.WARNING)
            for code in codes
        ),
    )


class _RecordingLauncher:
    def __init__(self) -> None:
        self.commands: list[MaintenanceCommand] = []

    def __call__(self, commands):
        self.commands = list(commands)
        return tuple(
            MaintenanceLaunch(command=c, pid=1000 + i, log_path=Path("/tmp/x.log"))
            for i, c in enumerate(commands)
        )


def test_selections_start_from_what_the_check_found(tmp_path: Path) -> None:
    """The operator confirms a diagnosis rather than repeating it."""
    controller = GraphMaintenanceWizardController(
        _session(tmp_path), _readiness("fulltext_indexes_missing")
    )

    values = controller.start().values

    assert values["create_fulltext_indexes"] is True
    assert values["create_functional_indexes"] is False


def test_running_only_indexes_needs_no_csv_step(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    controller = GraphMaintenanceWizardController(
        _session(tmp_path), _readiness("functional_indexes_missing"), launcher=launcher
    )
    controller.submit(
        {
            "load_relationship_classification": False,
            "create_fulltext_indexes": False,
            "create_functional_indexes": True,
            "cluster": True,
        }
    )

    snapshot = controller.review().snapshot
    assert isinstance(snapshot.step, ReviewStep)
    result = controller.apply()

    assert result.status is WizardResultStatus.APPLIED
    assert [c.argv[1:] for c in launcher.commands] == [("indexes", "enable", "--vocab")]


def test_selecting_relationship_tables_asks_for_the_csv_directory(
    tmp_path: Path,
) -> None:
    controller = GraphMaintenanceWizardController(_session(tmp_path), _readiness())
    transition = controller.submit(
        {
            "load_relationship_classification": True,
            "create_fulltext_indexes": False,
            "create_functional_indexes": False,
            "cluster": True,
        }
    )

    assert transition.snapshot.step.key == "sources"


def test_a_directory_without_the_csvs_is_rejected(tmp_path: Path) -> None:
    controller = GraphMaintenanceWizardController(_session(tmp_path), _readiness())
    controller.submit(
        {
            "load_relationship_classification": True,
            "create_fulltext_indexes": False,
            "create_functional_indexes": False,
            "cluster": True,
        }
    )

    controller.submit({"predicate_source": "custom"})
    transition = controller.submit({"predicate_csv_dir": str(tmp_path)})

    assert transition.issues
    assert "predicate_classification.csv" in transition.issues[0].message


def test_a_directory_with_the_csvs_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "predicates"
    source.mkdir()
    (source / "predicate_classification.csv").write_text("x", encoding="utf-8")
    (source / "predicate_mapping.csv").write_text("x", encoding="utf-8")
    launcher = _RecordingLauncher()
    controller = GraphMaintenanceWizardController(
        _session(tmp_path), _readiness(), launcher=launcher
    )
    controller.submit(
        {
            "load_relationship_classification": True,
            "create_fulltext_indexes": False,
            "create_functional_indexes": False,
            "cluster": True,
        }
    )
    controller.submit({"predicate_source": "custom"})
    controller.submit({"predicate_csv_dir": str(source)})

    assert controller.apply().status is WizardResultStatus.APPLIED
    assert str(source) in launcher.commands[0].argv


def test_selecting_nothing_is_refused_at_the_form(tmp_path: Path) -> None:
    controller = GraphMaintenanceWizardController(_session(tmp_path), _readiness())

    transition = controller.submit(
        {
            "load_relationship_classification": False,
            "create_fulltext_indexes": False,
            "create_functional_indexes": False,
            "cluster": True,
        }
    )

    assert transition.issues
    assert transition.snapshot.can_apply is False


def test_the_review_warns_about_cluster_and_the_two_stage_fulltext(
    tmp_path: Path,
) -> None:
    controller = GraphMaintenanceWizardController(_session(tmp_path), _readiness())
    controller.submit(
        {
            "load_relationship_classification": False,
            "create_fulltext_indexes": True,
            "create_functional_indexes": True,
            "cluster": True,
        }
    )

    step = controller.review().snapshot.step
    assert isinstance(step, ReviewStep)
    warnings = " ".join(step.review.warnings)
    assert "CLUSTER" in warnings
    assert "install then populate" in warnings


def test_cancelling_changes_nothing(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    controller = GraphMaintenanceWizardController(
        _session(tmp_path), _readiness(), launcher=launcher
    )

    result = controller.cancel()

    assert result.status is WizardResultStatus.CANCELLED
    assert launcher.commands == []


def test_the_bundled_classification_is_offered_as_a_choice(tmp_path: Path) -> None:
    """Accepting it must not mean reading an 80-character path in a text field."""
    from groundworkers.application.setup.graph_maintenance import (
        packaged_predicate_csv_dir,
    )

    controller = GraphMaintenanceWizardController(_session(tmp_path), _readiness())
    snapshot = controller.submit(
        {
            "load_relationship_classification": True,
            "create_fulltext_indexes": False,
            "create_functional_indexes": False,
            "cluster": True,
        }
    ).snapshot

    assert snapshot.step.key == "sources"
    field = next(f for f in snapshot.step.fields if f.key == "predicate_source")
    assert field.default == "bundled"
    assert str(packaged_predicate_csv_dir()) in field.help


def test_accepting_the_bundled_classification_skips_the_path_step(
    tmp_path: Path,
) -> None:
    launcher = _RecordingLauncher()
    controller = GraphMaintenanceWizardController(
        _session(tmp_path), _readiness(), launcher=launcher
    )
    controller.submit(
        {
            "load_relationship_classification": True,
            "create_fulltext_indexes": False,
            "create_functional_indexes": False,
            "cluster": True,
        }
    )

    review = controller.submit({"predicate_source": "bundled"}).snapshot

    assert isinstance(review.step, ReviewStep)
    controller.apply()
    from groundworkers.application.setup.graph_maintenance import (
        packaged_predicate_csv_dir,
    )

    assert str(packaged_predicate_csv_dir()) in launcher.commands[0].argv


def test_the_bundled_default_can_be_overridden(tmp_path: Path) -> None:
    """A site may maintain its own classification, so this stays a prompt."""
    source = tmp_path / "site-predicates"
    source.mkdir()
    (source / "predicate_classification.csv").write_text("x", encoding="utf-8")
    (source / "predicate_mapping.csv").write_text("x", encoding="utf-8")
    launcher = _RecordingLauncher()
    controller = GraphMaintenanceWizardController(
        _session(tmp_path), _readiness(), launcher=launcher
    )
    controller.submit(
        {
            "load_relationship_classification": True,
            "create_fulltext_indexes": False,
            "create_functional_indexes": False,
            "cluster": True,
        }
    )
    controller.submit({"predicate_source": "custom"})
    controller.submit({"predicate_csv_dir": str(source)})

    controller.apply()

    assert str(source) in launcher.commands[0].argv
