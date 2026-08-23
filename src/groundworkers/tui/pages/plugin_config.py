"""Top-level configuration page for one discovered Groundworkers plugin."""

from __future__ import annotations

from groundskeeping.configurator import (
    ConfigMutationService,
    ConfigWizardController,
    ConfigWorkflowSpec,
    UnavailableMutationService,
)
from groundskeeping.contracts import EmptyView, PageContext, PageRoute, ViewAction

from groundworkers.tui.pages.base import GroundworkersPage

_CONFIGURE_ACTION = "plugin.configure"


class PluginConfigPage(GroundworkersPage):
    """Expose one generic or plugin-supplied configuration workflow."""

    def __init__(
        self,
        route: PageRoute,
        workflow: ConfigWorkflowSpec,
        service: ConfigMutationService,
    ) -> None:
        super().__init__(route)
        self._workflow = workflow
        self._service = service

    def landing_view(self, context: PageContext) -> EmptyView:
        try:
            capabilities = self._service.capabilities(
                self._workflow.target,
                self._workflow.operation,
            )
            supported = capabilities.supported
            message = capabilities.reason
        except UnavailableMutationService as exc:
            supported = False
            message = str(exc) or "Plugin configuration is unavailable."
        return EmptyView(
            title=self.route.label,
            message=(
                "Configure this installed plugin through its package schema."
                if supported
                else message or "Plugin configuration is unavailable."
            ),
            actions=(
                ViewAction(
                    _CONFIGURE_ACTION,
                    "Configure",
                    variant="primary",
                    disabled=not supported,
                ),
            ),
        )

    def action_selected(self, action_key: str, context: PageContext) -> None:
        if action_key == _CONFIGURE_ACTION:
            context.open_wizard(
                ConfigWizardController(self._workflow, self._service)
            )


__all__ = ["PluginConfigPage"]
