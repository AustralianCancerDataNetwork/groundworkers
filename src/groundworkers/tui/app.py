from __future__ import annotations

import logging
from typing import Any

from groundworkers.application.setup.models import ConfigurationOwnership
from groundworkers.tui.state import SetupSession, load_tui_state

logger = logging.getLogger(__name__)


def build_groundworkers_tui_spec(
    *,
    config_path: str | None = None,
    ownership: ConfigurationOwnership | None = None,
):
    """Build the Groundworkers console with setup as its first registered page."""

    from groundskeeping.app import OperatorAppSpec
    from groundskeeping.configurator import MutationOperation
    from groundskeeping.contracts.pages import PageRegistration, PageRoute
    from groundskeeping.contracts.views import WorkbenchLabels

    from groundworkers.application.setup.plugin_configuration import (
        PackageConfigMutationService,
    )
    from groundworkers.plugins import (
        GroundworkersPluginConfigUI,
        discover_plugins,
    )
    from groundworkers.tui.pages import PluginConfigPage, SetupPage
    from groundworkers.tui.presenters.chat import ChatPresenter
    from groundworkers.tui.presenters.configuration import ConfigurationPresenter
    from groundworkers.tui.presenters.database import DatabasePresenter
    from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
    from groundworkers.tui.presenters.graph import GraphPresenter
    from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
    from groundworkers.tui.presenters.overview import OverviewPresenter
    from groundworkers.tui.presenters.performance import PerformancePresenter
    from groundworkers.tui.presenters.runs import RunsPresenter
    from groundworkers.tui.routes import SETUP_ROUTE

    session = load_tui_state(config_path=config_path, ownership=ownership)

    def setup_factory(_context: Any):
        return SetupPage(
            SETUP_ROUTE,
            session,
            database=DatabasePresenter(),
            overview=OverviewPresenter(),
            performance=PerformancePresenter(),
            graph=GraphPresenter(),
            llm_provider=LlmProviderPresenter(),
            embeddings=EmbeddingsPresenter(),
            chat=ChatPresenter(),
            configuration=ConfigurationPresenter(),
            runs=RunsPresenter(),
        )

    pages = [PageRegistration(route=SETUP_ROUTE, factory=setup_factory)]
    for plugin in discover_plugins():
        try:
            workflow_and_service = None
            if isinstance(plugin, GroundworkersPluginConfigUI):
                workflow_and_service = plugin.tui_workflow(MutationOperation.UPDATE)
            if workflow_and_service is None and plugin.config_cls is not None:
                service = PackageConfigMutationService(
                    session.configuration.path,
                    plugin.config_cls,
                    ownership=session.ownership,
                    on_applied=session.refresh_configuration,
                )
                workflow_and_service = (
                    service.workflow(MutationOperation.UPDATE),
                    service,
                )
        except Exception:
            logger.exception(
                "Could not build configuration page for plugin %s; skipping it.",
                plugin.name,
            )
            continue
        if workflow_and_service is None:
            continue
        workflow, service = workflow_and_service
        route = PageRoute(
            key=workflow.target.key,
            label=workflow.target.title,
            purpose=workflow.purpose,
        )

        def plugin_factory(
            _context: Any,
            *,
            route=route,
            workflow=workflow,
            service=service,
        ):
            return PluginConfigPage(route, workflow, service)

        pages.append(PageRegistration(route=route, factory=plugin_factory))

    return OperatorAppSpec(
        app_id="groundworkers",
        title="Groundworkers",
        subtitle="setup and operations",
        default_page=SETUP_ROUTE.key,
        pages=tuple(pages),
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

#wizard-body DataTable {
    height: auto;
    min-height: 4;
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
    ownership: ConfigurationOwnership | None = None,
) -> None:
    app_class = build_groundworkers_app()

    app_class(
        build_groundworkers_tui_spec(
            config_path=config_path,
            ownership=ownership,
        )
    ).run()


__all__ = [
    "SetupSession",
    "build_groundworkers_app",
    "build_groundworkers_tui_spec",
    "run_groundworkers_tui",
]
