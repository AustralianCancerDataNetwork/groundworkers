from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from oa_configurator import StackConfig


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
    profile: str | None
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
    resource_name: str
    database_name: str
    safe_url: str
    cdm_schema: str
    vocabulary_schema: str
    connection_url: str = field(repr=False)

    def __repr__(self) -> str:
        return (
            "DatabaseTarget("
            f"key={self.key!r}, label={self.label!r}, "
            f"resource_name={self.resource_name!r}, "
            f"database_name={self.database_name!r}, safe_url={self.safe_url!r}, "
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


@dataclass(frozen=True)
class ConnectionResult:
    target_key: str
    connected: bool
    latency_ms: float | None
    safe_url: str
    failure: ClassifiedFailure | None = None


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


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


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
