from __future__ import annotations

from pathlib import Path

from groundskeeping.contracts.views import SemanticStatus, TreeView
from oa_configurator.io import save_stack_config

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.tui.presenters.configuration import ConfigurationPresenter
from tests.support.stack_config import add_chat_model, build_embedding_stack


def _snapshot(tmp_path: Path, **tools: dict[str, object]):
    stack = build_embedding_stack()
    add_chat_model(stack, api_key="chat-secret")
    stack.connections["embedding_main"].password = "connection-secret"
    for name, section in tools.items():
        stack.tools[name] = section
    path = tmp_path / "config.toml"
    save_stack_config(stack, path)
    return load_configuration(config_path=path)


def test_configuration_section_renders_the_stack_as_a_tree(tmp_path: Path) -> None:
    view = ConfigurationPresenter().landing(_snapshot(tmp_path))

    assert isinstance(view, TreeView)
    rendered = repr(view)
    # Groundworkers' own section is typed straight from the omop.config
    # entry-point registry -- nothing is passed to the adapter by hand.
    assert "groundworkers" in rendered
    assert "embedding_model" in rendered


def test_configuration_section_redacts_by_declaration(tmp_path: Path) -> None:
    """Secrets are hidden because the schema marks them Sensitive(), not because
    this section knows which field names look secret."""
    view = ConfigurationPresenter().landing(_snapshot(tmp_path))

    rendered = repr(view)
    assert "chat-secret" not in rendered
    assert "connection-secret" not in rendered


def test_unregistered_tool_section_shows_structure_without_values(
    tmp_path: Path,
) -> None:
    """Without a schema there is no basis for calling a value safe to display."""
    snapshot = _snapshot(tmp_path, unregistered_pkg={"api_key": "leaked", "host": "h"})
    presenter = ConfigurationPresenter()

    view = presenter.landing(snapshot)

    assert "leaked" not in repr(view)
    # And the section reports the gap rather than passing silently.
    assert presenter.status(snapshot) is SemanticStatus.WARNING


def test_configuration_section_reports_an_unusable_config(tmp_path: Path) -> None:
    unreadable = load_configuration(config_path=tmp_path / "missing.toml")
    presenter = ConfigurationPresenter()

    assert presenter.status(unreadable) is SemanticStatus.ERROR
    assert not isinstance(presenter.landing(unreadable), TreeView)


def test_a_healthy_stack_reports_ok(tmp_path: Path) -> None:
    assert ConfigurationPresenter().status(_snapshot(tmp_path)) is SemanticStatus.OK
