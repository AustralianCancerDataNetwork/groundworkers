from __future__ import annotations

from groundskeeping.contracts import (
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
)

from groundworkers.application.setup.models import EmbeddingConfiguration


class EmbeddingsPresenter:
    def status(self, *, database_ready: bool, configured: bool) -> SemanticStatus:
        return (
            SemanticStatus.OK
            if database_ready and configured
            else SemanticStatus.WARNING
        )

    def landing(
        self,
        *,
        database_ready: bool,
        configuration: EmbeddingConfiguration | None,
    ) -> SurfaceView:
        if configuration is None:
            return EmptyView(
                title="Embedding setup not configured",
                message="Choose an embedding store, provider and model to continue.",
                status=SemanticStatus.WARNING,
            )
        return TableView(
            title="Embedding setup",
            columns=("Component", "Configuration", "Status"),
            rows=(
                TableRow(
                    key="embeddings.store",
                    cells=("Store", configuration.backend, "Not tested"),
                ),
                TableRow(
                    key="embeddings.provider",
                    cells=(
                        "Provider",
                        f"{configuration.provider_kind} · {configuration.api_base}",
                        "Not tested",
                    ),
                ),
                TableRow(
                    key="embeddings.model",
                    cells=("Model", configuration.model_name, "Not tested"),
                ),
            ),
            status=SemanticStatus.WARNING,
            message=(
                "Store reachability and encoding readiness are verified separately."
                if database_ready
                else "Configuration is visible, but database targets are not verified."
            ),
        )
