from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from oa_configurator import Resolver
from omop_emb.backends.index_config import FlatIndexConfig
from omop_emb.model_registry import EmbeddingModelRecord
from omop_emb.population import (
    EmbeddingPopulationPlan,
    PopulationScope,
    VocabularyPopulationPlan,
)
from omop_llm import Capabilities

from groundworkers.application.setup.embedding_population import (
    build_embedding_population_command,
    load_embedding_coverage_report,
)
from groundworkers.application.setup.embedding_setup import (
    load_embedding_configuration,
    probe_embedding_store,
    probe_provider,
    reconcile_models,
)
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationSnapshot,
    ConfigurationState,
    DiagnosticSeverity,
    EmbeddingConfiguration,
    EmbeddingIndexSnapshot,
    EmbeddingPopulationRequest,
    EmbeddingStoreState,
    ProviderCapabilities,
    ProviderSnapshot,
    RegisteredEmbeddingModel,
)
from tests.support.stack_config import build_embedding_stack


def _snapshot(stack, path: Path) -> ConfigurationSnapshot:
    stack.bind_loaded_path(path)
    return ConfigurationSnapshot(
        state=ConfigurationState.UNVERIFIED,
        path=path,
        ownership=ConfigurationOwnership(),
        stack=stack,
        revision="test-revision",
    )


def _resolved_model(stack):
    return Resolver(stack).resolve_model("embedding_model")


def _record(name: str = "test-model", dimensions: int = 3) -> EmbeddingModelRecord:
    return EmbeddingModelRecord(
        model_name=name,
        provider_type="openai",
        index_config=FlatIndexConfig(),
        dimensions=dimensions,
        storage_identifier="emb_test",
        created_at=datetime.now(UTC),
    )


class FakeStoreBackend:
    backend_type = "sqlitevec"

    def __init__(self, records=(), populated: bool = False) -> None:
        self.records = records
        self.populated = populated

    def get_registered_models(self):
        return tuple(self.records)

    def has_any_embeddings(self, *, model_name, metric_type, _model_record):
        assert model_name == _model_record.model_name
        assert metric_type is not None
        return self.populated


class FakeModelBackend:
    provider = "ollama"
    model = "qwen3-embedding:0.6b"

    def __init__(
        self,
        *,
        available: bool = True,
        embeddings: bool = True,
        dimensions: int = 1024,
        dimension_error: Exception | None = None,
    ) -> None:
        self.capabilities = Capabilities(embeddings=embeddings)
        self._available = available
        self._dimensions = dimensions
        self._dimension_error = dimension_error
        self.availability_calls = 0
        self.dimension_calls = 0

    def is_available(self) -> bool:
        self.availability_calls += 1
        return self._available

    def dimensions(self) -> int:
        self.dimension_calls += 1
        if self._dimension_error is not None:
            raise self._dimension_error
        return self._dimensions


def _configuration() -> EmbeddingConfiguration:
    return EmbeddingConfiguration(
        backend="pgvector",
        vector_store_name="embedding_store",
        database_name="embedding_db",
        connection_name="embedding_main",
        database_safe_url="postgresql://groundworkers:***@postgres/embeddings",
        provider_name="embedding_provider",
        provider_kind="ollama",
        model_entry_name="embedding_model",
        model_name="qwen3-embedding:0.6b",
        embeddings_supported=True,
        api_base="http://localhost:11434/v1",
    )


def test_reachable_store_with_no_models_is_empty_not_unavailable() -> None:
    snapshot = probe_embedding_store(
        lambda: FakeStoreBackend(),  # type: ignore[arg-type,return-value]
        backend_type="sqlitevec",
    )

    assert snapshot.reachable is True
    assert snapshot.state is EmbeddingStoreState.EMPTY


def test_embedding_configuration_requires_both_groundworkers_references(
    tmp_path: Path,
) -> None:
    stack = build_embedding_stack()
    stack.tools["groundworkers"].pop("embedding_model_name")

    result = load_embedding_configuration(_snapshot(stack, tmp_path / "config.toml"))

    assert result is None


def test_embedding_configuration_resolves_named_model_provider_and_store(
    tmp_path: Path,
) -> None:
    stack = build_embedding_stack()
    stack.connections["embedding_main"].database_name = "embeddings.db"
    stack.providers["embedding_provider"].base_url = (
        "https://provider.example/v1?api-key=super-secret&region=au"
    )
    stack.providers["embedding_provider"].api_key = "another-secret"
    (tmp_path / "embeddings.db").touch()

    result = load_embedding_configuration(_snapshot(stack, tmp_path / "config.toml"))

    assert result is not None
    assert result.vector_store_name == "embedding_store"
    assert result.backend == "sqlitevec"
    assert result.database_name == "embedding_db"
    assert result.connection_name == "embedding_main"
    assert result.database_path == "embeddings.db"
    assert result.database_path_exists is True
    assert result.provider_name == "embedding_provider"
    assert result.provider_kind == "ollama"
    assert result.model_entry_name == "embedding_model"
    assert result.model_name == "qwen3-embedding:0.6b"
    assert result.embeddings_supported is True
    # Every query value is masked, and "***" is no longer percent-encoded.
    assert result.api_base == "https://provider.example/v1?api-key=***&region=***"
    assert "super-secret" not in repr(result)
    assert "another-secret" not in repr(result)


def test_pgvector_configuration_uses_resolved_database_without_exposing_password(
    tmp_path: Path,
) -> None:
    stack = build_embedding_stack("pgvector")

    result = load_embedding_configuration(_snapshot(stack, tmp_path / "config.toml"))

    assert result is not None
    assert result.backend == "pgvector"
    assert result.database_path is None
    assert result.database_path_exists is None
    assert result.database_name == "embedding_db"
    assert result.database_safe_url
    assert "fixture-password" not in result.database_safe_url
    assert "fixture-password" not in repr(result)


def test_queryless_and_ipv6_provider_urls_are_safe_to_display() -> None:
    from oa_configurator import safe_endpoint

    assert safe_endpoint("http://localhost:11434/v1") == "http://localhost:11434/v1"
    # Every query value is masked, not just the ones a word list recognises, so
    # `region` is masked alongside `api_key`. IPv6 brackets survive.
    assert (
        safe_endpoint("http://[::1]:11434/v1?api_key=secret&region=local")
        == "http://[::1]:11434/v1?api_key=***&region=***"
    )


def test_reachable_store_separates_registration_from_population() -> None:
    empty = probe_embedding_store(
        lambda: FakeStoreBackend((_record(),), populated=False),  # type: ignore[arg-type,return-value]
        backend_type="sqlitevec",
    )
    populated = probe_embedding_store(
        lambda: FakeStoreBackend((_record(),), populated=True),  # type: ignore[arg-type,return-value]
        backend_type="sqlitevec",
    )

    assert empty.state is EmbeddingStoreState.EMPTY
    assert empty.models[0].concept_count == 0
    assert populated.state is EmbeddingStoreState.POPULATED
    assert populated.models[0].concept_count is None


def test_provider_probe_uses_public_reachability_capability_and_dimensions(
    tmp_path: Path,
) -> None:
    stack = build_embedding_stack()
    resolved = _resolved_model(stack)
    backend = FakeModelBackend()

    snapshot = probe_provider(
        resolved,
        backend_factory=lambda _: backend,  # type: ignore[arg-type,return-value]
        inventory_discoverer=lambda _: ("qwen3-embedding:0.6b",),
    )

    assert snapshot.provider_name == "embedding_provider"
    assert snapshot.provider_kind == "ollama"
    assert snapshot.model_entry_name == "embedding_model"
    assert snapshot.reachable is True
    assert snapshot.encoding_succeeded is True
    assert snapshot.inventory == ("qwen3-embedding:0.6b",)
    assert snapshot.dimensions == 1024
    assert backend.availability_calls == 1
    assert backend.dimension_calls == 1


def test_provider_probe_reports_non_embedding_model_without_encoding() -> None:
    stack = build_embedding_stack()
    backend = FakeModelBackend(embeddings=False)

    snapshot = probe_provider(
        _resolved_model(stack),
        backend_factory=lambda _: backend,  # type: ignore[arg-type,return-value]
    )

    assert snapshot.reachable is True
    assert snapshot.encoding_succeeded is False
    assert snapshot.capabilities.encode_probe is False
    assert backend.dimension_calls == 0


def test_provider_probe_stops_before_dimensions_when_provider_is_unreachable() -> None:
    stack = build_embedding_stack()
    backend = FakeModelBackend(available=False)

    snapshot = probe_provider(
        _resolved_model(stack),
        backend_factory=lambda _: backend,  # type: ignore[arg-type,return-value]
    )

    assert snapshot.reachable is False
    assert snapshot.encoding_succeeded is False
    assert snapshot.failure is not None
    assert backend.dimension_calls == 0


def test_provider_dimension_failure_and_inventory_failure_are_secret_safe() -> None:
    stack = build_embedding_stack()
    backend = FakeModelBackend(dimension_error=RuntimeError("provider-secret"))

    def inventory_failure(_resolved):
        raise RuntimeError("inventory-secret")

    snapshot = probe_provider(
        _resolved_model(stack),
        backend_factory=lambda _: backend,  # type: ignore[arg-type,return-value]
        inventory_discoverer=inventory_failure,
    )

    assert snapshot.reachable is True
    assert snapshot.encoding_succeeded is False
    assert snapshot.inventory is None
    assert snapshot.failure is not None
    assert "provider-secret" not in repr(snapshot)
    assert "inventory-secret" not in repr(snapshot)


def test_provider_probe_failure_is_secret_safe() -> None:
    stack = build_embedding_stack()
    stack.providers["embedding_provider"].api_key = "provider-secret"

    def fail(_resolved):
        raise RuntimeError("provider-secret")

    snapshot = probe_provider(_resolved_model(stack), backend_factory=fail)

    assert snapshot.reachable is False
    assert snapshot.failure is not None
    assert "provider-secret" not in repr(snapshot)
    assert "provider-secret" not in snapshot.failure.detail


def test_reconciliation_catches_dimension_mismatch() -> None:
    model = RegisteredEmbeddingModel(
        model_name="test-model",
        provider="openai",
        dimensions=4,
        metric=None,
        index_type="flat",
        has_embeddings=True,
    )
    provider = ProviderSnapshot(
        provider_name="provider",
        provider_kind="openai",
        model_entry_name="model",
        api_base="https://provider.example/v1",
        configured_model="test-model",
        capabilities=ProviderCapabilities(),
        reachable=True,
        encoding_succeeded=True,
        dimensions=3,
    )

    result = reconcile_models(
        configured_model="test-model",
        registered_models=(model,),
        provider=provider,
    )

    mismatch = next(
        item for item in result.diagnostics if item.code == "dimension_mismatch"
    )
    assert mismatch.severity is DiagnosticSeverity.ERROR
    assert result.ready_for_population is False


def test_reconciliation_distinguishes_selected_missing_and_other_models() -> None:
    selected = RegisteredEmbeddingModel(
        model_name="selected-model",
        provider="ollama",
        dimensions=3,
        metric="cosine",
        index_type="flat",
        has_embeddings=True,
    )
    other = RegisteredEmbeddingModel(
        model_name="other-model",
        provider="ollama",
        dimensions=3,
        metric="cosine",
        index_type="flat",
        has_embeddings=True,
    )

    selected_result = reconcile_models(
        configured_model="selected-model",
        registered_models=(selected, other),
        provider=None,
    )
    missing_result = reconcile_models(
        configured_model="missing-model",
        registered_models=(selected,),
        provider=None,
    )

    selected_codes = {item.code for item in selected_result.diagnostics}
    missing_codes = {item.code for item in missing_result.diagnostics}
    assert "configured_model_unregistered" not in selected_codes
    assert "registered_model_not_selected" in selected_codes
    assert "configured_model_unregistered" in missing_codes


def test_reconciliation_reports_model_without_embedding_capability() -> None:
    provider = ProviderSnapshot(
        provider_name="provider",
        provider_kind="ollama",
        model_entry_name="model",
        api_base=None,
        configured_model="chat-model:1",
        capabilities=ProviderCapabilities(encode_probe=False),
        reachable=True,
        encoding_succeeded=False,
    )

    result = reconcile_models(
        configured_model="chat-model:1",
        registered_models=(),
        provider=provider,
    )

    capability = next(
        item
        for item in result.diagnostics
        if item.code == "provider_embeddings_unsupported"
    )
    assert capability.severity is DiagnosticSeverity.ERROR


def test_population_command_uses_omop_emb_2_model_entry_and_no_profile(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population.shutil.which",
        lambda _name: "omop-emb",
    )

    command = build_embedding_population_command(
        _configuration(),
        EmbeddingPopulationRequest(
            standard_only=True,
            vocabulary_mode="selected",
            vocabularies=("SNOMED", "LOINC"),
            limit=500,
            batch_size=64,
        ),
        config_path="/tmp/config.toml",
    )

    assert command.argv == (
        "omop-emb",
        "embeddings",
        "add-embeddings",
        "--model-name",
        "embedding_model",
        "--batch-size",
        "64",
        "--standard-only",
        "--vocabulary",
        "SNOMED",
        "--vocabulary",
        "LOINC",
        "--num-embeddings",
        "500",
    )
    assert command.environment == (("OA_CONFIG_PATH", "/tmp/config.toml"),)
    assert "OA_ACTIVE_PROFILE" not in command.display
    assert "provider-secret" not in command.display


def test_coverage_resolves_public_backend_and_canonical_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stack = build_embedding_stack()
    snapshot = _snapshot(stack, tmp_path / "config.toml")
    captured: dict[str, object] = {}

    class DisposableEngine:
        def dispose(self) -> None:
            captured["cdm_disposed"] = True

    class ReadOnlyStore:
        def close(self) -> None:
            captured["store_closed"] = True

    store = ReadOnlyStore()
    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population.inspect_resolved_vector_store",
        lambda resolved: captured.setdefault("store", resolved) and store,
    )
    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population._cdm_engine_and_schema",
        lambda _snapshot: (DisposableEngine(), "main"),
    )
    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population.plan_population",
        lambda *_args, **_kwargs: EmbeddingPopulationPlan(
            model_name="canonical-model",
            scope=PopulationScope(standard_only=True),
            rows=(
                VocabularyPopulationPlan(
                    vocabulary="SNOMED",
                    eligible_ids=frozenset(range(10)),
                    compatible_ids=frozenset(range(4)),
                    missing_ids=frozenset(range(4, 10)),
                    stale_ids=frozenset(),
                    metadata_changed_ids=frozenset(),
                ),
            ),
            store_initialized=True,
        ),
    )

    def canonical(model_name, *, provider_kind):
        captured["canonical"] = (provider_kind, model_name)
        return "canonical-model"

    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population._canonical_model_name",
        canonical,
    )
    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population._embedding_index_snapshot",
        lambda **_kwargs: EmbeddingIndexSnapshot(
            model_name="canonical-model",
            registered=True,
            storage_identifier="emb_canonical",
            registry_metric="cosine",
        ),
    )
    report = load_embedding_coverage_report(snapshot)

    assert report is not None
    assert report.coverage.available is True
    assert report.coverage.pending_total == 6
    assert report.coverage.scope.model_name == "canonical-model"
    assert captured["canonical"] == ("ollama", "qwen3-embedding:0.6b")
    assert captured["cdm_disposed"] is True
    assert captured["store_closed"] is True


def test_coverage_failure_does_not_expose_backend_secret(
    monkeypatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(build_embedding_stack(), tmp_path / "config.toml")

    def fail(_store):
        raise RuntimeError("database-password")

    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population.inspect_resolved_vector_store",
        fail,
    )

    report = load_embedding_coverage_report(snapshot)

    assert report is not None
    assert report.coverage.available is False
    assert report.coverage.blocker is not None
    assert "database-password" not in report.coverage.blocker
