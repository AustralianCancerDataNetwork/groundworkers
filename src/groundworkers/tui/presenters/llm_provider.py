from __future__ import annotations

from groundskeeping.contracts import (
    DetailView,
    EmptyView,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    TextView,
    ViewAction,
)

from groundworkers.application.setup.models import (
    DiagnosticSeverity,
    LlmModelMetadata,
    LlmProviderCheckResult,
    LlmProviderConfiguration,
)
from groundworkers.tui.presenters.base import SetupPresenterBase


class LlmProviderPresenter(SetupPresenterBase):
    def status(
        self,
        configuration: LlmProviderConfiguration | None,
        result: LlmProviderCheckResult | None = None,
    ) -> SemanticStatus:
        if configuration is None or not configuration.enabled:
            return SemanticStatus.WARNING
        if result is None:
            return SemanticStatus.WARNING
        if result.ready:
            return SemanticStatus.OK
        if result.has_errors:
            return SemanticStatus.ERROR
        return SemanticStatus.WARNING

    def landing(
        self,
        configuration: LlmProviderConfiguration | None,
        result: LlmProviderCheckResult | None = None,
    ) -> SurfaceView:
        if configuration is None or not configuration.enabled:
            return EmptyView(
                title="LLM provider not configured",
                message="Configure a provider endpoint before selecting a chat model.",
                status=SemanticStatus.WARNING,
                actions=(
                    ViewAction(
                        "llm_provider.configure",
                        "Configure",
                        variant="primary",
                    ),
                ),
            )
        rows = [
            TableRow(
                key="llm.provider",
                cells=("Provider", configuration.provider, "Configured"),
            ),
            TableRow(
                key="llm.endpoint",
                cells=(
                    "Endpoint",
                    configuration.api_base or "Provider default",
                    _endpoint_status(result),
                ),
            ),
            TableRow(
                key="llm.credentials",
                cells=(
                    "Credentials",
                    "Configured"
                    if configuration.credentials_configured
                    else "Not supplied",
                    "Not tested" if result is None else "Used",
                ),
            ),
            TableRow(
                key="llm.model",
                cells=(
                    "Default model",
                    configuration.default_model_name or "Not selected",
                    _model_status(configuration, result),
                ),
            ),
        ]
        rows.extend(_diagnostic_rows(result))
        return TableView(
            title="LLM provider",
            columns=("Setting", "Value", "Status"),
            rows=tuple(rows),
            status=self.status(configuration, result),
            message=_message(result),
            actions=(
                ViewAction(
                    "llm_provider.configure",
                    "Configure",
                    variant="primary",
                ),
                ViewAction("llm_provider.test", "Test provider"),
            ),
        )

    def detail(
        self,
        configuration: LlmProviderConfiguration | None,
        result: LlmProviderCheckResult | None = None,
    ) -> DetailView:
        if configuration is None or not configuration.enabled:
            return TextView(
                title="Model inventory",
                body="LLM provider is not configured.",
            )
        if result is None:
            return TextView(
                title="Model inventory",
                body="Run Test provider to load the model inventory.",
            )
        if result.inventory is None:
            return TextView(
                title="Model inventory",
                body=_inventory_unavailable_detail(result),
            )
        if not result.inventory:
            return TextView(
                title="Model inventory",
                body="No models were returned by the provider.",
            )
        metadata_by_name = {item.name: item for item in result.model_metadata}
        return TableView(
            title="Model inventory",
            columns=(
                "Model",
                "Size",
                "Params",
                "Quant",
                "Family",
                "Format",
                "Modified",
                "Digest",
            ),
            rows=tuple(
                _model_inventory_row(model, metadata_by_name.get(model))
                for model in result.inventory
            ),
            status=SemanticStatus.OK if result.reachable else SemanticStatus.ERROR,
        )


def _endpoint_status(result: LlmProviderCheckResult | None) -> str:
    if result is None:
        return "Not tested"
    return "Connected" if result.reachable else "Failed"


def _model_status(
    configuration: LlmProviderConfiguration,
    result: LlmProviderCheckResult | None,
) -> str:
    if not configuration.default_model_name:
        return "Missing"
    if result is None:
        return "Not tested"
    if result.model_available is True:
        return "Available"
    if result.model_available is False:
        return "Unavailable"
    return "Not checked"


def _diagnostic_rows(
    result: LlmProviderCheckResult | None,
) -> tuple[TableRow, ...]:
    if result is None:
        return (
            TableRow(
                key="llm.inventory",
                cells=("Model inventory", "Not checked", "Not tested"),
            ),
        )
    rows = [
        TableRow(
            key="llm.inventory",
            cells=(
                "Model inventory",
                f"{len(result.inventory)} model(s)"
                if result.inventory is not None
                else "Unavailable",
                "Connected" if result.reachable else "Failed",
            ),
        )
    ]
    for index, diagnostic in enumerate(result.diagnostics, start=1):
        rows.append(
            TableRow(
                key=f"llm.diagnostic.{index}",
                cells=(
                    _diagnostic_label(diagnostic.severity),
                    diagnostic.message,
                    diagnostic.severity.value.title(),
                ),
            )
        )
    return tuple(rows)


def _diagnostic_label(severity: DiagnosticSeverity) -> str:
    if severity is DiagnosticSeverity.ERROR:
        return "Error"
    if severity is DiagnosticSeverity.WARNING:
        return "Warning"
    return "Check"


def _message(result: LlmProviderCheckResult | None) -> str:
    if result is None:
        return (
            "Test the provider endpoint and selected model before editing this setup."
        )
    if result.ready:
        return "Provider endpoint and selected model are ready."
    return "Resolve provider connectivity or model selection before editing this setup."


def _model_inventory_row(
    model_name: str,
    metadata: LlmModelMetadata | None,
) -> TableRow:
    if metadata is None:
        return TableRow(
            key=f"llm.inventory.{model_name}",
            cells=(model_name, "", "", "", "", "", "", ""),
        )
    return TableRow(
        key=f"llm.inventory.{model_name}",
        cells=(
            model_name,
            _format_size(metadata.size_bytes)
            if metadata.size_bytes is not None
            else "",
            metadata.parameter_size or "",
            metadata.quantization_level or "",
            metadata.family or "",
            metadata.format or "",
            metadata.modified_at[:10] if metadata.modified_at is not None else "",
            metadata.digest[:12] if metadata.digest is not None else "",
        ),
    )


def _inventory_unavailable_detail(result: LlmProviderCheckResult) -> str:
    lines = ["Model inventory is unavailable."]
    if result.failure is not None:
        lines.append(f"Failure: {result.failure.kind.value.replace('_', ' ').title()}")
        lines.append(result.failure.detail)
        lines.append(result.failure.next_action)
    if result.diagnostics:
        lines.append("")
        lines.append("Diagnostics")
        lines.extend(
            f"{diagnostic.severity.value}: {diagnostic.message}"
            for diagnostic in result.diagnostics
        )
    return "\n".join(lines)


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
