from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from oa_configurator import StackConfig  # type: ignore[import-untyped]


class ConfigurationState(StrEnum):
    MISSING = "missing"
    MALFORMED = "malformed"
    INCOMPLETE = "incomplete"
    UNVERIFIED = "unverified"


class OwnershipMode(StrEnum):
    AUTHORITATIVE = "authoritative"
    DERIVED_READ_ONLY = "derived_read_only"


@dataclass(frozen=True)
class ConfigurationOwnership:
    mode: OwnershipMode = OwnershipMode.AUTHORITATIVE
    source_label: str = "Local stack configuration"
    guidance: str = "This file can be edited by the setup console."
    inferred: bool = False

    @property
    def editable(self) -> bool:
        return self.mode is OwnershipMode.AUTHORITATIVE


@dataclass(frozen=True)
class SetupIssue:
    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class ConfigurationSnapshot:
    state: ConfigurationState
    path: Path
    ownership: ConfigurationOwnership
    stack: StackConfig | None = field(default=None, repr=False)
    revision: str | None = None
    issues: tuple[SetupIssue, ...] = ()

    @property
    def usable(self) -> bool:
        return self.state is ConfigurationState.UNVERIFIED


@dataclass(frozen=True)
class ConfigurationSaveResult:
    snapshot: ConfigurationSnapshot
    backup_path: Path | None
    replaced_existing: bool
    restart_required: bool = True


@dataclass(frozen=True)
class DatabaseTarget:
    key: str
    label: str
    database_entry_name: str
    connection_name: str
    safe_url: str
    cdm_schema: str
    vocabulary_schema: str
    connection_url: str = field(repr=False)
    role: str = "cdm"
    expected_embedding_model_name: str | None = None
    embedding_safe_url: str | None = None
    embedding_connection_url: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (
            "DatabaseTarget("
            f"key={self.key!r}, label={self.label!r}, "
            f"database_entry_name={self.database_entry_name!r}, "
            f"connection_name={self.connection_name!r}, safe_url={self.safe_url!r}, "
            f"cdm_schema={self.cdm_schema!r}, "
            f"vocabulary_schema={self.vocabulary_schema!r})"
        )


class ConnectionFailureKind(StrEnum):
    DNS = "dns"
    REFUSED = "connection_refused"
    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    DATABASE_MISSING = "database_missing"
    DRIVER_MISSING = "driver_missing"
    QUERY = "query_failure"
    OTHER = "other"


@dataclass(frozen=True)
class ClassifiedFailure:
    kind: ConnectionFailureKind
    detail: str
    next_action: str


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class ResourceDiagnostic:
    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO


@dataclass(frozen=True)
class ConnectionResult:
    target_key: str
    connected: bool
    latency_ms: float | None
    safe_url: str
    failure: ClassifiedFailure | None = None
    diagnostics: tuple[ResourceDiagnostic, ...] = ()

    @property
    def has_warnings(self) -> bool:
        return any(
            diagnostic.severity is DiagnosticSeverity.WARNING
            for diagnostic in self.diagnostics
        )


@dataclass(frozen=True)
class GraphConfiguration:
    resource_name: str
    max_depth: int
    max_paths: int


@dataclass(frozen=True)
class LlmProviderConfiguration:
    enabled: bool
    provider: str
    api_base: str | None
    credentials_configured: bool
    default_model_name: str | None

    @property
    def ready_for_chat(self) -> bool:
        return self.enabled and self.default_model_name is not None


@dataclass(frozen=True)
class LlmModelMetadata:
    name: str
    size_bytes: int | None = None
    modified_at: str | None = None
    digest: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    family: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class LlmProviderCheckResult:
    provider: str
    api_base: str | None
    default_model_name: str | None
    reachable: bool
    model_available: bool | None = None
    inventory: tuple[str, ...] | None = None
    model_metadata: tuple[LlmModelMetadata, ...] = ()
    failure: ClassifiedFailure | None = None
    diagnostics: tuple[ResourceDiagnostic, ...] = ()

    @property
    def has_warnings(self) -> bool:
        return any(
            diagnostic.severity is DiagnosticSeverity.WARNING
            for diagnostic in self.diagnostics
        )

    @property
    def has_errors(self) -> bool:
        return any(
            diagnostic.severity is DiagnosticSeverity.ERROR
            for diagnostic in self.diagnostics
        )

    @property
    def ready(self) -> bool:
        return self.reachable and self.model_available is True and not self.has_errors


@dataclass(frozen=True)
class ChatConfiguration:
    provider: str
    model_name: str


class EmbeddingStoreState(StrEnum):
    UNCONFIGURED = "unconfigured"
    UNREACHABLE = "unreachable"
    EMPTY = "empty"
    POPULATED = "populated"


@dataclass(frozen=True)
class EmbeddingConfiguration:
    backend: str
    provider_kind: str
    model_name: str
    api_base: str
    sqlite_path: str | None = None
    sqlite_path_exists: bool | None = None
    faiss_cache_dir: str | None = None
    faiss_cache_dir_exists: bool | None = None


@dataclass(frozen=True)
class RegisteredEmbeddingModel:
    model_name: str
    provider: str
    dimensions: int
    metric: str | None
    index_type: str
    has_embeddings: bool
    concept_count: int | None = None


@dataclass(frozen=True)
class EmbeddingStoreSnapshot:
    state: EmbeddingStoreState
    backend: str | None
    reachable: bool
    models: tuple[RegisteredEmbeddingModel, ...] = ()
    failure: ClassifiedFailure | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    probe: bool = True
    list_models: bool = False
    encode_probe: bool = True
    pull_model: bool = False
    reported_dimensions: bool = False


@dataclass(frozen=True)
class ProviderSnapshot:
    provider_kind: str
    api_base: str
    configured_model: str
    capabilities: ProviderCapabilities
    reachable: bool
    encoding_succeeded: bool
    dimensions: int | None = None
    inventory: tuple[str, ...] | None = None
    failure: ClassifiedFailure | None = None

    @property
    def model_available(self) -> bool | None:
        if self.inventory is None:
            return True if self.encoding_succeeded else None
        return self.configured_model in self.inventory


@dataclass(frozen=True)
class ModelDiagnostic:
    code: str
    severity: DiagnosticSeverity
    message: str


@dataclass(frozen=True)
class ModelReconciliation:
    configured_model: str | None
    registered_models: tuple[RegisteredEmbeddingModel, ...]
    provider: ProviderSnapshot | None
    diagnostics: tuple[ModelDiagnostic, ...]

    @property
    def ready_for_population(self) -> bool:
        return not any(
            item.severity is DiagnosticSeverity.ERROR for item in self.diagnostics
        )


@dataclass(frozen=True)
class ArtifactMetadata:
    path: Path
    model_name: str
    dimensions: int
    metric: str
    provider: str
    row_count: int
    vocabularies: tuple[str, ...] | None = None
    standard_only: bool | None = None
    valid_only: bool | None = None


@dataclass(frozen=True)
class ArtifactCompatibility:
    compatible: bool
    issues: tuple[SetupIssue, ...]


@dataclass(frozen=True)
class ArtifactDiscovery:
    artifacts: tuple[ArtifactMetadata, ...]
    issues: tuple[SetupIssue, ...] = ()


@dataclass(frozen=True)
class CoverageScope:
    model_name: str
    metric: str
    vocabularies: tuple[str, ...]
    domains: tuple[str, ...] = ()
    standard_only: bool = True
    valid_only: bool = True


@dataclass(frozen=True)
class VocabularyCoverage:
    vocabulary: str
    eligible: int
    embedded: int
    pending: int
    coverage_percent: float


@dataclass(frozen=True)
class CoverageSnapshot:
    scope: CoverageScope
    available: bool
    rows: tuple[VocabularyCoverage, ...] = ()
    eligible_total: int = 0
    embedded_total: int = 0
    pending_total: int = 0
    blocker: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingIndexSnapshot:
    model_name: str
    registered: bool
    storage_identifier: str | None = None
    registry_index_type: str | None = None
    registry_metric: str | None = None
    physical_indexes: tuple[str, ...] = ()
    drop_sql: tuple[str, ...] = ()

    @property
    def has_physical_index(self) -> bool:
        return bool(self.physical_indexes)

    @property
    def display(self) -> str:
        if not self.registered:
            return "Unregistered"
        index_type = (self.registry_index_type or "unknown").upper()
        metric = f" {self.registry_metric}" if self.registry_metric else ""
        if self.registry_index_type == "flat" and self.has_physical_index:
            return "Registry FLAT; physical index present"
        if self.registry_index_type == "flat":
            return "FLAT / exact scan"
        if self.registry_index_type and self.has_physical_index:
            return f"{index_type}{metric}"
        return f"{index_type}{metric} missing"

    @property
    def insert_warning(self) -> str | None:
        if not self.has_physical_index:
            return None
        return (
            "Adding embeddings over an existing physical vector index will be slow. "
            "Drop the index before a large run, then rebuild it afterwards."
        )


@dataclass(frozen=True)
class EmbeddingCoverageReport:
    configuration: EmbeddingConfiguration
    coverage: CoverageSnapshot
    index: EmbeddingIndexSnapshot

    @property
    def incomplete_vocabularies(self) -> tuple[str, ...]:
        return tuple(row.vocabulary for row in self.coverage.rows if row.pending > 0)


@dataclass(frozen=True)
class EmbeddingPopulationRequest:
    standard_only: bool
    vocabulary_mode: str
    vocabularies: tuple[str, ...]
    limit: int | None
    batch_size: int


@dataclass(frozen=True)
class EmbeddingPopulationCommand:
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()

    @property
    def display(self) -> str:
        parts = [f"{key}={_shell_quote(value)}" for key, value in self.environment]
        parts.extend(_shell_quote(part) for part in self.argv)
        return " ".join(parts)


@dataclass(frozen=True)
class EmbeddingPopulationLaunch:
    command: EmbeddingPopulationCommand
    pid: int
    log_path: Path


def _shell_quote(value: str) -> str:
    if value and all(ch.isalnum() or ch in "-_./:=+" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
