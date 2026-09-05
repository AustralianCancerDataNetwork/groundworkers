"""Top-level configuration page for one discovered Groundworkers plugin."""

from __future__ import annotations

from collections.abc import Callable

from groundskeeping.configurator import (
    ConfigMutationService,
    ConfigWizardController,
    ConfigWorkflowSpec,
    UnavailableMutationService,
)
from groundskeeping.contracts import (
    EmptyView,
    KeyValueView,
    PageContext,
    PageRoute,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    ViewAction,
)

from groundworkers.plugins import (
    PluginReadinessField,
    PluginReadinessResult,
    PluginReadinessState,
)
from groundworkers.tui.pages.base import GroundworkersPage

_CONFIGURE_ACTION = "plugin.configure"
_VERIFY_ACTION = "plugin.verify"


class PluginConfigPage(GroundworkersPage):
    """Expose one generic or plugin-supplied configuration workflow."""

    def __init__(
        self,
        route: PageRoute,
        workflow: ConfigWorkflowSpec,
        service: ConfigMutationService,
        readiness: Callable[[], PluginReadinessResult] | None = None,
    ) -> None:
        super().__init__(route)
        self._workflow = workflow
        self._service = service
        self._readiness = readiness
        self._last_readiness: PluginReadinessResult | None = None

    def landing_view(self, context: PageContext) -> SurfaceView:
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
        actions = self._actions(configure_supported=supported)
        if self._readiness is None:
            return EmptyView(
                title=self.route.label,
                message=(
                    "Configure this installed plugin through its package schema."
                    if supported
                    else message or "Plugin configuration is unavailable."
                ),
                actions=actions,
            )

        self._last_readiness = self._load_readiness()
        result = self._last_readiness
        if not result.fields:
            return EmptyView(
                title=self.route.label,
                message=result.summary,
                status=_semantic_status(result.state),
                actions=actions,
            )
        return _readiness_view(self.route.label, result, actions)

    def action_selected(self, action_key: str, context: PageContext) -> None:
        if action_key == _CONFIGURE_ACTION:
            context.open_wizard(ConfigWizardController(self._workflow, self._service))
        elif action_key == _VERIFY_ACTION and self._readiness is not None:
            self._last_readiness = self._load_readiness()
            context.surface.refresh_view(
                self.route.key,
                _readiness_surface(
                    self.route.label,
                    self._last_readiness,
                    self._actions(configure_supported=self._configure_supported()),
                ),
            )
            context.notify(
                self._last_readiness.summary,
                severity=(
                    "error"
                    if self._last_readiness.state is PluginReadinessState.ERROR
                    else "information"
                ),
            )

    def row_highlighted(self, row_key: str, context: PageContext) -> None:
        if self._last_readiness is None:
            return
        field = next(
            (item for item in self._last_readiness.fields if item.key == row_key),
            None,
        )
        if field is None:
            return
        context.surface.show_detail(
            self.route.key,
            KeyValueView(
                title=f"{field.label} detail",
                rows=(
                    ("Status", field.state.value),
                    ("Value", field.value),
                    ("Detail", field.detail or "No additional detail."),
                ),
            ),
        )

    def _actions(self, *, configure_supported: bool) -> tuple[ViewAction, ...]:
        actions = [
            ViewAction(
                _CONFIGURE_ACTION,
                "Configure",
                variant="primary",
                disabled=not configure_supported,
            )
        ]
        if self._readiness is not None:
            actions.append(ViewAction(_VERIFY_ACTION, "Verify"))
        return tuple(actions)

    def _configure_supported(self) -> bool:
        try:
            return self._service.capabilities(
                self._workflow.target,
                self._workflow.operation,
            ).supported
        except UnavailableMutationService:
            return False

    def _load_readiness(self) -> PluginReadinessResult:
        try:
            assert self._readiness is not None
            return self._readiness()
        except Exception:
            return PluginReadinessResult(
                state=PluginReadinessState.ERROR,
                summary="Plugin verification failed unexpectedly; review the host logs.",
            )


def _readiness_surface(
    title: str,
    result: PluginReadinessResult,
    actions: tuple[ViewAction, ...],
) -> SurfaceView:
    if not result.fields:
        return EmptyView(
            title=title,
            message=result.summary,
            status=_semantic_status(result.state),
            actions=actions,
        )
    return _readiness_view(title, result, actions)


def _readiness_view(
    title: str,
    result: PluginReadinessResult,
    actions: tuple[ViewAction, ...],
) -> TableView:
    return TableView(
        title=title,
        columns=("Check", "Value", "Status"),
        rows=tuple(
            TableRow(
                key=field.key,
                cells=(field.label, field.value, _field_status(field)),
            )
            for field in result.fields
        ),
        status=_semantic_status(result.state),
        message=result.summary,
        actions=actions,
    )


def _field_status(field: PluginReadinessField) -> str:
    return field.state.value.replace("_", " ").title()


def _semantic_status(state: PluginReadinessState) -> SemanticStatus:
    return {
        PluginReadinessState.UNCONFIGURED: SemanticStatus.WARNING,
        PluginReadinessState.WARNING: SemanticStatus.WARNING,
        PluginReadinessState.READY: SemanticStatus.OK,
        PluginReadinessState.ERROR: SemanticStatus.ERROR,
    }[state]


__all__ = ["PluginConfigPage"]
