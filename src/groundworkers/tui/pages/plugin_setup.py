"""TUI page exposing one generic plugin setup operation."""

from groundskeeping.contracts import EmptyView, PageContext, PageRoute, ViewAction

from groundworkers.plugins import PluginSetupStep
from groundworkers.tui.pages.base import GroundworkersPage
from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.plugin_setup import PluginSetupWizardController


class PluginSetupPage(GroundworkersPage):
    """Show the setup operation and open its host-owned argument wizard."""

    def __init__(self, route: PageRoute, session: SetupSession, step: PluginSetupStep) -> None:
        super().__init__(route)
        self._session = session
        self._step = step

    def landing_view(self, context: PageContext) -> EmptyView:
        return EmptyView(
            title=self._step.title,
            message=(
                f"{self._step.purpose} Arguments are requested for each run and "
                "are not persisted as plugin configuration."
            ),
            actions=(ViewAction("plugin.setup.run", self._step.apply_label, variant="primary"),),
        )

    def action_selected(self, action_key: str, context: PageContext) -> None:
        if action_key == "plugin.setup.run":
            context.open_wizard(PluginSetupWizardController(self._session, self._step))


__all__ = ["PluginSetupPage"]
