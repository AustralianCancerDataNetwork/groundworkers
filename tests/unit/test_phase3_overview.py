from __future__ import annotations

from pathlib import Path

from groundskeeping.contracts import EmptyView, TableView
from groundskeeping.contracts.views import SemanticStatus
from oa_configurator.io import save_stack_config

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.integration import build_integration_output
from groundworkers.application.setup.models import ConnectionResult
from groundworkers.tui.presenters.overview import OverviewPresenter
from tests.support.stack_config import build_cdm_stack


def test_missing_configuration_landing_is_a_cdm_recovery_action(tmp_path: Path) -> None:
    snapshot = load_configuration(config_path=tmp_path / "missing.toml")

    view = OverviewPresenter().landing(
        snapshot,
        connections=(),
        embedding_coverage=None,
        llm_result=None,
        graph_ready=False,
        integration_ready=False,
    )

    assert isinstance(view, EmptyView)
    assert view.title == "Start Groundworkers"
    assert view.actions[0].key == "database.configure"


def test_cdm_readiness_keeps_optional_capabilities_neutral_and_emits_integration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)
    snapshot = load_configuration(config_path=path)
    connected = (
        ConnectionResult(
            target_key="database.cdm",
            connected=True,
            latency_ms=1.0,
            safe_url="sqlite://",
        ),
    )

    presenter = OverviewPresenter()
    view = presenter.landing(
        snapshot,
        connections=connected,
        embedding_coverage=None,
        llm_result=None,
        graph_ready=False,
        integration_ready=True,
    )
    output = build_integration_output(snapshot)

    assert isinstance(view, TableView)
    assert view.status is SemanticStatus.OK
    assert "Not configured or unchecked" in repr(view)
    assert output is not None
    assert output.stdio_command == f"groundworkers --config-path {path} --transport stdio"
    assert output.http_command.endswith(
        "--transport streamable-http --host 127.0.0.1 --port 8000"
    )
