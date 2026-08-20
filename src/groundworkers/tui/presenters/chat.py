from __future__ import annotations

from groundskeeping.contracts import (
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
)

from groundworkers.application.setup.models import ChatConfiguration
from groundworkers.tui.presenters.base import SetupPresenterBase


class ChatPresenter(SetupPresenterBase):
    def status(self, configuration: ChatConfiguration | None) -> SemanticStatus:
        return SemanticStatus.OK if configuration is not None else SemanticStatus.WARNING

    def landing(self, configuration: ChatConfiguration | None) -> SurfaceView:
        if configuration is None:
            return EmptyView(
                title="Chat model not selected",
                message="Enable an LLM provider and select its default chat model.",
                status=SemanticStatus.WARNING,
            )
        return TableView(
            title="Chat model",
            columns=("Check", "Selection", "Status"),
            rows=(
                TableRow(
                    key="chat.model",
                    cells=("Default model", configuration.model_name, "Configured"),
                ),
                TableRow(
                    key="chat.completion",
                    cells=("Chat completion", configuration.provider, "Not tested"),
                ),
                TableRow(
                    key="chat.structured",
                    cells=("Structured output", configuration.provider, "Not tested"),
                ),
            ),
            status=self.status(configuration),
            message="Checks use the selected model's configured capabilities.",
        )
