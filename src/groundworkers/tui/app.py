from __future__ import annotations

from typing import Any

from groundworkers.tui.state import SetupSession, load_tui_state


def build_groundworkers_tui_spec(
    *,
    config_path: str | None = None,
):
    """Build the Groundworkers console with setup as its first registered page."""

    from groundskeeping.app import OperatorAppSpec
    from groundskeeping.contracts.pages import PageRegistration
    from groundskeeping.contracts.views import WorkbenchLabels

    from groundworkers.tui.pages import SetupPage
    from groundworkers.tui.presenters.chat import ChatPresenter
    from groundworkers.tui.presenters.configuration import ConfigurationPresenter
    from groundworkers.tui.presenters.database import DatabasePresenter
    from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
    from groundworkers.tui.presenters.graph import GraphPresenter
    from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
    from groundworkers.tui.routes import SETUP_ROUTE

    session = load_tui_state(config_path=config_path)

    def setup_factory(_context: Any):
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

    return OperatorAppSpec(
        app_id="groundworkers",
        title="Groundworkers",
        subtitle="setup and operations",
        default_page=SETUP_ROUTE.key,
        pages=(PageRegistration(route=SETUP_ROUTE, factory=setup_factory),),
        workbench_labels=WorkbenchLabels(result_panel="Setup"),
        metadata={
            "config_path": str(session.configuration.path),
            "config_state": session.configuration.state.value,
        },
    )


# Groundskeeping's theme sizes every TextArea at `height: 1fr`, which is right
# for the workbench context pane it was written for and wrong inside a wizard:
# the labels, help lines, and sibling fields take their auto height first, and
# the fraction left over rounds to zero rows. The field is still focusable and
# still editable -- it is simply not drawn, so it reads as an entry box that
# refuses to accept typing. A type selector cannot be overridden by adding
# another type selector, so this qualifies on the wizard body's id.
#
# Remove once groundskeeping sizes wizard fields itself; the upstream fix wants
# the rule scoped to `#context` rather than to every TextArea in the app.
_WIZARD_FIELD_CSS = """
#wizard-body TextArea {
    height: 8;
    max-height: 8;
    border: tall $panel;
}

#wizard-body TextArea:focus {
    border: tall $accent;
}
"""


def build_groundworkers_app():
    """Build the console app class, with wizard field sizing repaired."""

    from pathlib import Path

    import groundskeeping
    from groundskeeping.app import OperatorApp

    # Textual resolves a relative CSS_PATH against the module defining the
    # class, so a subclass declared here would look for groundskeeping's theme
    # under groundworkers and fail to start. Re-anchor it on the package that
    # actually ships the file.
    theme_path = Path(groundskeeping.__file__).parent / OperatorApp.CSS_PATH

    class GroundworkersApp(OperatorApp):
        CSS_PATH = str(theme_path)
        CSS = _WIZARD_FIELD_CSS

    return GroundworkersApp


def run_groundworkers_tui(
    *,
    config_path: str | None = None,
) -> None:
    app_class = build_groundworkers_app()

    app_class(build_groundworkers_tui_spec(config_path=config_path)).run()


__all__ = [
    "SetupSession",
    "build_groundworkers_app",
    "build_groundworkers_tui_spec",
    "run_groundworkers_tui",
]
