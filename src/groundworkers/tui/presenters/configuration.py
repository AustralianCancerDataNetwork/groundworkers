from __future__ import annotations

from groundskeeping.configurator.adapter import OAConfiguratorAdapter
from groundskeeping.contracts.views import EmptyView, SemanticStatus, SurfaceView

from groundworkers.application.setup.models import ConfigurationSnapshot
from groundworkers.tui.presenters.base import SetupPresenterBase

_STATUS_PRECEDENCE = (SemanticStatus.ERROR, SemanticStatus.WARNING)


class ConfigurationPresenter(SetupPresenterBase):
    """Structural view of the stack config, as opposed to a liveness check.

    Every other section on this page reports whether something *works* --
    whether a database answers, whether a provider is reachable. This one
    reports what the configuration *says*: which entries Groundworkers
    references and whether each reference resolves.

    The rendering is groundskeeping's ``OAConfiguratorAdapter``, which types
    each ``[tools.*]`` section from the ``omop.config`` entry-point registry and
    redacts by the schema's own ``Sensitive()`` markers. A section whose package
    registers no config class is shown as a key count only, since without a
    schema there is no basis for deciding its values are safe to display.
    """

    def __init__(self, adapter: OAConfiguratorAdapter | None = None) -> None:
        self._adapter = adapter or OAConfiguratorAdapter()

    def status(self, snapshot: ConfigurationSnapshot) -> SemanticStatus:
        configurator = self._snapshot(snapshot)
        if configurator is None:
            return SemanticStatus.ERROR
        statuses = {section.target.status for section in configurator.sections}
        for candidate in _STATUS_PRECEDENCE:
            if candidate in statuses:
                return candidate
        return SemanticStatus.OK

    def landing(self, snapshot: ConfigurationSnapshot) -> SurfaceView:
        configurator = self._snapshot(snapshot)
        if configurator is None:
            return EmptyView(
                title="Configuration unavailable",
                message="Resolve the configuration issues before inspecting the stack.",
                status=SemanticStatus.ERROR,
            )
        return self._adapter.as_tree_view(configurator)

    def _snapshot(self, snapshot: ConfigurationSnapshot):
        if not snapshot.usable or snapshot.stack is None:
            return None
        return self._adapter.snapshot(
            snapshot.stack,
            config_path=snapshot.path,
            title="Stack configuration",
        )
