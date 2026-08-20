from __future__ import annotations

from pathlib import Path

import pytest
from groundskeeping.contracts.wizards import ReviewStep, WizardResultStatus
from oa_configurator import save_stack_config

from groundworkers.application.setup.models import ConfigurationState
from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.config_location import ConfigLocationWizardController
from tests.support.stack_config import build_cdm_stack


def _drive_to_review(controller: ConfigLocationWizardController, path: str | None = None):
    start = controller.start()
    values = {"config_path": path if path is not None else start.values["config_path"]}
    return controller.submit(values)


def test_defaults_to_the_path_the_session_would_have_used(tmp_path: Path) -> None:
    """Accepting the default must be a single confirmation, not retyping a path."""
    session = SetupSession(config_path=tmp_path / "config.toml")

    snapshot = ConfigLocationWizardController(session).start()

    assert snapshot.values["config_path"] == str(tmp_path / "config.toml")


def test_accepting_the_default_leaves_the_path_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    session = SetupSession(config_path=target)
    controller = ConfigLocationWizardController(session)

    _drive_to_review(controller)
    result = controller.apply()

    assert result.status is WizardResultStatus.APPLIED
    assert Path(session.config_path) == target


def test_pointing_at_an_existing_config_loads_it(tmp_path: Path) -> None:
    existing = tmp_path / "elsewhere" / "config.toml"
    existing.parent.mkdir()
    save_stack_config(build_cdm_stack(), existing)
    session = SetupSession(config_path=tmp_path / "config.toml")
    assert session.configuration.state is ConfigurationState.MISSING

    controller = ConfigLocationWizardController(session)
    _drive_to_review(controller, str(existing))
    result = controller.apply()

    assert result.status is WizardResultStatus.APPLIED
    # The point of the wizard: the regular workflows now act on the real file.
    assert session.configuration.state is ConfigurationState.UNVERIFIED
    assert session.configuration.path == existing


def test_review_says_whether_a_config_is_being_adopted_or_created(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing.toml"
    save_stack_config(build_cdm_stack(), existing)
    session = SetupSession(config_path=tmp_path / "config.toml")

    adopting = ConfigLocationWizardController(session)
    step = _drive_to_review(adopting, str(existing)).snapshot.step
    assert isinstance(step, ReviewStep)
    assert "Load the existing configuration." in step.review.effects

    creating = ConfigLocationWizardController(session)
    step = _drive_to_review(creating, str(tmp_path / "new.toml")).snapshot.step
    assert isinstance(step, ReviewStep)
    assert "Start a new configuration at this path." in step.review.effects


def test_a_path_whose_parent_is_missing_is_allowed_but_flagged(tmp_path: Path) -> None:
    """save_stack_config creates parents, so this is a warning, not a rejection."""
    session = SetupSession(config_path=tmp_path / "config.toml")
    controller = ConfigLocationWizardController(session)

    step = _drive_to_review(controller, str(tmp_path / "does" / "not" / "exist.toml")).snapshot.step

    assert isinstance(step, ReviewStep)
    assert step.review.warnings
    assert step.review.ready_to_apply is True


@pytest.mark.parametrize("bad", ["", "   "])
def test_an_empty_path_is_rejected(tmp_path: Path, bad: str) -> None:
    controller = ConfigLocationWizardController(SetupSession(config_path=tmp_path / "c.toml"))

    transition = _drive_to_review(controller, bad)

    assert transition.issues
    assert transition.issues[0].field_key == "config_path"


def test_a_directory_is_rejected(tmp_path: Path) -> None:
    controller = ConfigLocationWizardController(SetupSession(config_path=tmp_path / "c.toml"))

    transition = _drive_to_review(controller, str(tmp_path))

    assert transition.issues
    assert "directory" in transition.issues[0].message


def test_cancelling_keeps_the_current_location(tmp_path: Path) -> None:
    """Cancelling must leave a usable console, not a dead end."""
    target = tmp_path / "config.toml"
    session = SetupSession(config_path=target)
    controller = ConfigLocationWizardController(session)

    result = controller.cancel()

    assert result.status is WizardResultStatus.CANCELLED
    assert Path(session.config_path) == target


# ---------------------------------------------------------------------------
# Startup trigger
# ---------------------------------------------------------------------------


class _RecordingContext:
    """Minimal PageContext stand-in; only the wizard hook is exercised."""

    def __init__(self) -> None:
        self.wizards: list[object] = []
        self.surface = _NullSurface()

    def open_wizard(self, controller: object) -> None:
        self.wizards.append(controller)

    def notify(self, message: str, *, severity: str = "information") -> None:
        pass

    def refresh_bindings(self) -> None:
        pass

    def request_navigation(self, page_key: str) -> None:
        pass


class _NullSurface:
    def show_view(self, *args: object, **kwargs: object) -> None:
        pass

    def show_detail(self, *args: object, **kwargs: object) -> None:
        pass

    def show_actions(self, *args: object, **kwargs: object) -> None:
        pass


def _page(session: SetupSession):
    from groundworkers.tui.pages import SetupPage
    from groundworkers.tui.presenters.chat import ChatPresenter
    from groundworkers.tui.presenters.configuration import ConfigurationPresenter
    from groundworkers.tui.presenters.database import DatabasePresenter
    from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
    from groundworkers.tui.presenters.graph import GraphPresenter
    from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
    from groundworkers.tui.routes import SETUP_ROUTE

    return SetupPage(
        SETUP_ROUTE,
        session,
        database=DatabasePresenter(),
        graph=GraphPresenter(),
        llm_provider=LlmProviderPresenter(),
        embeddings=EmbeddingsPresenter(),
        chat=ChatPresenter(),
        configuration=ConfigurationPresenter(),
    )


def test_a_missing_config_opens_the_location_wizard_on_activate(tmp_path: Path) -> None:
    page = _page(SetupSession(config_path=tmp_path / "config.toml"))
    context = _RecordingContext()

    page.activate(context)

    assert len(context.wizards) == 1
    assert isinstance(context.wizards[0], ConfigLocationWizardController)


def test_the_location_wizard_is_offered_once_not_on_every_activate(
    tmp_path: Path,
) -> None:
    """Re-prompting would make the page unreachable."""
    page = _page(SetupSession(config_path=tmp_path / "config.toml"))
    context = _RecordingContext()

    page.activate(context)
    page.activate(context)

    assert len(context.wizards) == 1


def test_an_existing_config_goes_straight_to_the_page(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), config_path)
    page = _page(SetupSession(config_path=config_path))
    context = _RecordingContext()

    page.activate(context)

    assert context.wizards == []
