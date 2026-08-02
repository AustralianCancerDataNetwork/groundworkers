from __future__ import annotations

from groundskeeping.contracts import (
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
)

from groundworkers.application.setup.models import LlmProviderConfiguration


class LlmProviderPresenter:
    def status(self, configuration: LlmProviderConfiguration | None) -> SemanticStatus:
        if configuration is None or not configuration.enabled:
            return SemanticStatus.WARNING
        return SemanticStatus.WARNING

    def landing(self, configuration: LlmProviderConfiguration | None) -> SurfaceView:
        if configuration is None or not configuration.enabled:
            return EmptyView(
                title="LLM provider not configured",
                message="Configure a provider endpoint before selecting a chat model.",
                status=SemanticStatus.WARNING,
            )
        return TableView(
            title="LLM provider",
            columns=("Setting", "Value", "Status"),
            rows=(
                TableRow(
                    key="llm.provider",
                    cells=("Provider", configuration.provider, "Configured"),
                ),
                TableRow(
                    key="llm.endpoint",
                    cells=(
                        "Endpoint",
                        configuration.api_base or "Provider default",
                        "Configured",
                    ),
                ),
                TableRow(
                    key="llm.credentials",
                    cells=(
                        "Credentials",
                        "Configured"
                        if configuration.credentials_configured
                        else "Not supplied",
                        "Not tested",
                    ),
                ),
                TableRow(
                    key="llm.inventory",
                    cells=("Model inventory", "Not checked", "Not tested"),
                ),
            ),
            status=self.status(configuration),
            message="Connectivity and model inventory are verified at the provider boundary.",
        )
