from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from omop_emb.config import ProviderType
from omop_emb.model_registry import EmbeddingModelRecord
from omop_emb.backends.index_config import FlatIndexConfig

from groundworkers.application.setup.embedding_setup import (
    load_embedding_configuration,
    probe_embedding_store,
    probe_provider,
    reconcile_models,
    safe_api_base,
)
from groundworkers.application.setup.embedding_population import (
    build_embedding_population_command,
)
from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.models import (
    DiagnosticSeverity,
    EmbeddingConfiguration,
    EmbeddingPopulationRequest,
    EmbeddingStoreState,
    ProviderCapabilities,
    ProviderSnapshot,
    RegisteredEmbeddingModel,
)


def _record(name: str = "test-model", dimensions: int = 3) -> EmbeddingModelRecord:
    return EmbeddingModelRecord(
        model_name=name,
        provider_type=ProviderType.OPENAI,
        index_config=FlatIndexConfig(),
        dimensions=dimensions,
        storage_identifier="emb_test",
        created_at=datetime.now(UTC),
    )


class FakeBackend:
    backend_type = "sqlitevec"

    def __init__(self, records=(), populated: bool = False) -> None:
        self.records = records
        self.populated = populated

    def get_registered_models(self):
        return tuple(self.records)

    def has_any_embeddings(self, **_kwargs):
        return self.populated


class FakeProvider:
    provider_kind = "openai"
    api_base = "https://provider.example/v1"
    model_name = "test-model"
    capabilities = ProviderCapabilities(list_models=False)

    def list_models(self):
        raise AssertionError("inventory should not be requested")

    def encode_probe(self):
        return 3


def test_reachable_store_with_no_models_is_empty_not_unavailable() -> None:
    snapshot = probe_embedding_store(lambda: FakeBackend(), backend_type="sqlitevec")

    assert snapshot.reachable is True
    assert snapshot.state is EmbeddingStoreState.EMPTY


def test_embedding_defaults_do_not_imply_tool_is_configured(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"
[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"
""",
        encoding="utf-8",
    )

    result = load_embedding_configuration(load_configuration(config_path=path))

    assert result is None


def test_embedding_api_base_is_loaded_and_query_secrets_are_redacted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"

[tools.omop_emb.extra]
backend = "sqlitevec"
sqlite_path = "embeddings.db"
provider_type = "openai"
api_base = "https://provider.example/v1?api-key=super-secret&region=au"
api_key = "another-secret"
embedding_model = "test-model"
""",
        encoding="utf-8",
    )

    result = load_embedding_configuration(load_configuration(config_path=path))

    assert result is not None
    assert result.api_base == "https://provider.example/v1?api-key=%2A%2A%2A&region=au"
    assert "super-secret" not in repr(result)
    assert "another-secret" not in repr(result)


def test_embedding_configuration_reports_relative_sqlite_and_faiss_paths(
    tmp_path: Path,
) -> None:
    sqlite_path = tmp_path / "embeddings.db"
    cache_dir = tmp_path / "faiss-cache"
    sqlite_path.touch()
    cache_dir.mkdir()
    path = tmp_path / "config.toml"
    path.write_text(
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"

[tools.omop_emb.extra]
backend = "sqlitevec"
sqlite_path = "embeddings.db"
faiss_cache_dir = "faiss-cache"
embedding_model = "test-model"
""",
        encoding="utf-8",
    )

    result = load_embedding_configuration(load_configuration(config_path=path))

    assert result is not None
    assert result.backend == "sqlitevec"
    assert result.sqlite_path_exists is True
    assert result.faiss_cache_dir == "faiss-cache"
    assert result.faiss_cache_dir_exists is True


def test_faiss_backend_reports_actionable_configuration_issue(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"

[tools.omop_emb.extra]
backend = "faiss"
embedding_model = "test-model"
""",
        encoding="utf-8",
    )

    snapshot = load_configuration(config_path=path)

    issue = next(
        item for item in snapshot.issues if item.code == "embedding_backend_invalid"
    )
    assert "FAISS is a cache accelerator" in issue.message
    assert "faiss_cache_dir" in issue.message

    configuration = load_embedding_configuration(snapshot)

    assert configuration is not None
    assert configuration.backend == "faiss"


def test_queryless_embedding_api_base_is_safe_to_display() -> None:
    assert safe_api_base("http://localhost:11434/v1") == "http://localhost:11434/v1"


def test_ipv6_embedding_api_base_is_safe_to_display() -> None:
    assert (
        safe_api_base("http://[::1]:11434/v1?api_key=secret&region=local")
        == "http://[::1]:11434/v1?api_key=%2A%2A%2A&region=local"
    )


def test_reachable_store_separates_registration_from_population() -> None:
    empty = probe_embedding_store(
        lambda: FakeBackend((_record(),), populated=False), backend_type="sqlitevec"
    )
    populated = probe_embedding_store(
        lambda: FakeBackend((_record(),), populated=True), backend_type="sqlitevec"
    )

    assert empty.state is EmbeddingStoreState.EMPTY
    assert empty.models[0].concept_count == 0
    assert populated.state is EmbeddingStoreState.POPULATED
    assert populated.models[0].concept_count is None


def test_provider_inventory_can_be_unsupported_when_encoding_succeeds() -> None:
    snapshot = probe_provider(FakeProvider())

    assert snapshot.reachable is True
    assert snapshot.encoding_succeeded is True
    assert snapshot.inventory is None
    assert snapshot.dimensions == 3


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
        provider_kind="openai",
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


def test_population_command_uses_omop_emb_filters(monkeypatch) -> None:
    monkeypatch.setattr(
        "groundworkers.application.setup.embedding_population.shutil.which",
        lambda _name: "omop-emb",
    )

    command = build_embedding_population_command(
        EmbeddingConfiguration(
            backend="pgvector",
            provider_kind="ollama",
            model_name="qwen3-embedding:0.6b",
            api_base="http://localhost:11434/v1",
        ),
        EmbeddingPopulationRequest(
            standard_only=True,
            vocabulary_mode="selected",
            vocabularies=("SNOMED", "LOINC"),
            limit=500,
            batch_size=64,
        ),
        config_path="/tmp/config.toml",
        profile="local",
    )

    assert command.argv == (
        "omop-emb",
        "embeddings",
        "add-embeddings",
        "--model",
        "qwen3-embedding:0.6b",
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
    assert command.environment == (
        ("OA_CONFIG_PATH", "/tmp/config.toml"),
        ("OA_ACTIVE_PROFILE", "local"),
    )
    assert command.display.startswith(
        "OA_CONFIG_PATH=/tmp/config.toml OA_ACTIVE_PROFILE=local "
    )
