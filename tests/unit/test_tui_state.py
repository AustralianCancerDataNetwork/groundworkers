from __future__ import annotations

from pathlib import Path

from groundworkers.application.setup.models import ConfigurationState
from groundworkers.tui.presenters.database import DatabasePresenter
from groundworkers.tui.state import SetupSession


def test_setup_session_starts_with_missing_config() -> None:
    session = SetupSession(
        config_path="/definitely/not/a/groundworkers-config.toml",
        profile="test",
    )

    assert session.configuration.state is ConfigurationState.MISSING
    assert session.databases_connected is False


def test_malformed_config_is_presented_without_secret(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[databases.main]\npassword = "super-secret"\nbroken = [\n',
        encoding="utf-8",
    )
    session = SetupSession(config_path=path)

    view = DatabasePresenter().landing(session.configuration, (), ())

    assert session.configuration.state is ConfigurationState.MALFORMED
    assert "super-secret" not in repr(view)


def test_refresh_discards_stale_connection_results(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    session = SetupSession(config_path=path)
    session.connection_results = (object(),)  # type: ignore[assignment]

    session.refresh_configuration()

    assert session.connection_results == ()
