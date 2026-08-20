from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("groundskeeping")  # setup write flows live behind the `tui` extra

from groundskeeping.contracts import TableView

from groundworkers.application.setup.models import (
    ClassifiedFailure,
    ConfigurationState,
    ConnectionFailureKind,
    ConnectionResult,
    CoverageScope,
    CoverageSnapshot,
    DatabaseTarget,
    DiagnosticSeverity,
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
    EmbeddingIndexSnapshot,
    LlmModelMetadata,
    LlmProviderCheckResult,
    LlmProviderConfiguration,
    ResourceDiagnostic,
    VocabularyCoverage,
)
from groundworkers.tui.presenters.database import DatabasePresenter
from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
from groundworkers.tui.state import SetupSession


def _embedding_configuration(**overrides) -> EmbeddingConfiguration:
    values = {
        "backend": "sqlitevec",
        "vector_store_name": "embedding_store",
        "database_name": "embedding_db",
        "connection_name": "embedding_main",
        "database_safe_url": "sqlite:///embeddings.db",
        "provider_name": "embedding_provider",
        "provider_kind": "ollama",
        "model_entry_name": "embedding_model",
        "model_name": "qwen3-embedding:0.6b",
        "embeddings_supported": True,
        "api_base": "http://localhost:11434/v1",
    }
    values.update(overrides)
    return EmbeddingConfiguration(**values)


def test_setup_session_starts_with_missing_config() -> None:
    session = SetupSession(
        config_path="/definitely/not/a/groundworkers-config.toml",
    )

    assert session.configuration.state is ConfigurationState.MISSING
    assert session.databases_connected is False


def test_malformed_config_is_presented_without_secret(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[databases.main]\npassword = "super-secret"\nbroken = [\n',
        encoding="utf-8",
    )
    session = SetupSession(config_path=path)

    view = DatabasePresenter().landing(session.configuration, (), ())

    assert session.configuration.state is ConfigurationState.MALFORMED
    assert "super-secret" not in repr(view)


def test_refresh_discards_stale_connection_results(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    session = SetupSession(config_path=path)
    session.connection_results = (object(),)  # type: ignore[assignment]

    session.refresh_configuration()

    assert session.connection_results == ()


def test_database_failure_status_uses_classified_kind(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )
    session = SetupSession(config_path=path)
    target = DatabaseTarget(
        key="database.cdm",
        label="CDM / vocabulary",
        database_entry_name="cdm_db",
        connection_name="main",
        safe_url="sqlite:///:memory:",
        cdm_schema="main",
        vocabulary_schema="main",
        connection_url="sqlite:///:memory:",
    )
    view = DatabasePresenter().landing(
        session.configuration,
        (target,),
        (
            ConnectionResult(
                target_key="database.cdm",
                connected=False,
                latency_ms=None,
                safe_url="sqlite:///:memory:",
                failure=ClassifiedFailure(
                    ConnectionFailureKind.AUTHENTICATION,
                    "The service rejected the configured user or credentials.",
                    "Check the configured user.",
                ),
            ),
        ),
    )

    assert view.rows[0].cells[3] == "Authentication"


def test_database_warning_status_uses_successful_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )
    session = SetupSession(config_path=path)
    target = DatabaseTarget(
        key="database.cdm",
        label="CDM / vocabulary",
        database_entry_name="cdm_db",
        connection_name="main",
        safe_url="sqlite:///:memory:",
        cdm_schema="main",
        vocabulary_schema="main",
        connection_url="sqlite:///:memory:",
    )
    view = DatabasePresenter().landing(
        session.configuration,
        (target,),
        (
            ConnectionResult(
                target_key="database.cdm",
                connected=True,
                latency_ms=4.2,
                safe_url="sqlite:///:memory:",
                diagnostics=(
                    ResourceDiagnostic(
                        "cdm_tables_missing",
                        "Connected, but CDM tables are missing.",
                        DiagnosticSeverity.WARNING,
                    ),
                ),
            ),
        ),
    )

    assert view.status.name == "WARNING"
    assert view.rows[0].cells[3] == "Warnings"
    assert str(view.rows[0].detail[0][0]) == "✓"
    assert view.rows[0].detail[0][1] == "Connected: sqlite:///:memory:"
    assert str(view.rows[0].detail[1][0]) == "!"
    assert view.rows[0].detail[1][1] == "Connected, but CDM tables are missing."


def test_embeddings_presenter_shows_sqlitevec_and_faiss_paths() -> None:
    view = EmbeddingsPresenter().landing(
        database_ready=True,
        configuration=_embedding_configuration(
            database_path="embeddings.db",
            database_path_exists=True,
            faiss_cache_dir="faiss-cache",
            faiss_cache_dir_exists=False,
        ),
    )

    assert view.status.name == "WARNING"
    assert view.rows[0].cells == (
        "Store",
        "embedding_store · sqlitevec · embeddings.db",
        "Found",
    )
    assert view.rows[1].cells == (
        "Provider",
        "embedding_provider (ollama) · http://localhost:11434/v1",
        "Not checked",
    )
    assert view.rows[2].cells == (
        "Model",
        "embedding_model · qwen3-embedding:0.6b",
        "Not checked",
    )
    assert view.rows[3].cells == ("FAISS cache", "faiss-cache", "Missing")


def test_embeddings_presenter_explains_faiss_is_not_a_backend() -> None:
    view = EmbeddingsPresenter().landing(
        database_ready=True,
        configuration=_embedding_configuration(
            backend="faiss",
        ),
    )

    assert view.status.name == "ERROR"
    assert view.rows[0].cells == (
        "Store",
        "embedding_store · faiss",
        "Unsupported",
    )
    assert "sqlitevec or pgvector" in str(view.message)


def test_embeddings_presenter_foregrounds_index_warning_and_drop_sql() -> None:
    coverage = CoverageSnapshot(
        scope=CoverageScope(
            model_name="qwen3-embedding:0.6b",
            metric="cosine",
            vocabularies=("SNOMED",),
            standard_only=True,
            valid_only=False,
        ),
        available=True,
        rows=(
            VocabularyCoverage(
                vocabulary="SNOMED",
                eligible=10,
                embedded=4,
                pending=6,
                coverage_percent=40.0,
            ),
        ),
        eligible_total=10,
        embedded_total=4,
        pending_total=6,
    )
    configuration = _embedding_configuration(
        backend="pgvector",
    )
    report = EmbeddingCoverageReport(
        configuration=configuration,
        coverage=coverage,
        index=EmbeddingIndexSnapshot(
            model_name="qwen3-embedding:0.6b",
            registered=True,
            storage_identifier="emb_qwen3",
            registry_index_type="flat",
            physical_indexes=("idx_emb_qwen3_cosine",),
            drop_sql=("DROP INDEX IF EXISTS idx_emb_qwen3_cosine;",),
        ),
    )
    presenter = EmbeddingsPresenter()
    view = presenter.landing(
        database_ready=True,
        configuration=configuration,
        coverage=report,
    )
    detail = presenter.detail(configuration, report)

    assert isinstance(view, TableView)
    assert view.status.name == "WARNING"
    assert view.title == "Embedding setup"
    assert view.rows[3].cells == (
        "Index",
        "Registry FLAT; physical index present",
        "Warning",
    )
    assert view.rows[-3].cells == (
        "! Index warning",
        "Adding over an existing physical vector index will be slow.",
        "Drop before large runs",
    )
    assert view.rows[-1].cells == (
        "  Drop SQL 1",
        "DROP INDEX IF EXISTS idx_emb_qwen3_cosine;",
        "Suggested",
    )
    populate = next(a for a in view.actions if a.key == "embeddings.populate")
    assert populate.label == "Populate"
    assert populate.disabled is False
    assert isinstance(detail, TableView)
    assert detail.title == "Vocabulary coverage"
    assert detail.columns == (
        "Vocabulary",
        "CDM",
        "Vector store",
        "Missing",
        "Coverage",
    )
    assert detail.rows[0].cells == ("All vocabularies", "10", "4", "6", "40.0%")
    assert detail.rows[1].cells == (
        "SNOMED",
        "10",
        "4",
        "6",
        "40.0%",
    )


def test_llm_provider_presenter_allows_configure_before_probe() -> None:
    configuration = LlmProviderConfiguration(
        enabled=True,
        provider="openai-compatible",
        api_base="https://provider.example/v1",
        credentials_configured=True,
        default_model_name="chat-model",
    )
    presenter = LlmProviderPresenter()

    untested = presenter.landing(configuration)

    assert untested.status.name == "WARNING"
    assert untested.rows[3].cells == ("Default model", "chat-model", "Not tested")
    assert untested.actions[0].label == "Configure"
    assert untested.actions[0].disabled is False

    ready = presenter.landing(
        configuration,
        LlmProviderCheckResult(
            provider="openai-compatible",
            api_base="https://provider.example/v1",
            default_model_name="chat-model",
            reachable=True,
            model_available=True,
            inventory=("chat-model",),
            diagnostics=(
                ResourceDiagnostic("llm_endpoint_reachable", "Endpoint reached."),
                ResourceDiagnostic("llm_model_available", "Model available."),
            ),
        ),
    )

    assert ready.status.name == "OK"
    assert ready.rows[1].cells[2] == "Connected"
    assert ready.rows[3].cells[2] == "Available"
    assert ready.actions[0].disabled is False


def test_llm_provider_detail_reports_model_inventory() -> None:
    configuration = LlmProviderConfiguration(
        enabled=True,
        provider="openai-compatible",
        api_base="https://provider.example/v1",
        credentials_configured=True,
        default_model_name="chat-model",
    )
    presenter = LlmProviderPresenter()

    untested = presenter.detail(configuration)

    assert untested.title == "Model inventory"
    assert untested.body == "Run Test provider to load the model inventory."

    checked = presenter.detail(
        configuration,
        LlmProviderCheckResult(
            provider="openai-compatible",
            api_base="https://provider.example/v1",
            default_model_name="chat-model",
            reachable=True,
            model_available=True,
            inventory=("chat-model", "other-model"),
            model_metadata=(
                LlmModelMetadata(
                    name="chat-model",
                    size_bytes=4_294_967_296,
                    parameter_size="7B",
                    quantization_level="Q4_K_M",
                    family="llama",
                    format="gguf",
                    modified_at="2026-08-01T12:00:00Z",
                    digest="abcdef1234567890",
                ),
            ),
        ),
    )

    assert checked.columns == (
        "Model",
        "Size",
        "Params",
        "Quant",
        "Family",
        "Format",
        "Modified",
        "Digest",
    )
    assert checked.rows[0].cells == (
        "chat-model",
        "4.0 GB",
        "7B",
        "Q4_K_M",
        "llama",
        "gguf",
        "2026-08-01",
        "abcdef123456",
    )
    assert checked.rows[1].cells == (
        "other-model",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    )


def test_llm_provider_detail_reports_inventory_failure() -> None:
    configuration = LlmProviderConfiguration(
        enabled=True,
        provider="ollama",
        api_base="http://localhost:11434/v1",
        credentials_configured=True,
        default_model_name="chat-model",
    )
    presenter = LlmProviderPresenter()

    detail = presenter.detail(
        configuration,
        LlmProviderCheckResult(
            provider="ollama",
            api_base="http://localhost:11434/v1",
            default_model_name="chat-model",
            reachable=False,
            failure=ClassifiedFailure(
                ConnectionFailureKind.REFUSED,
                "The provider endpoint did not respond.",
                "Check that Ollama is running.",
            ),
            diagnostics=(
                ResourceDiagnostic(
                    "llm_endpoint_unreachable",
                    "The provider endpoint did not respond.",
                    DiagnosticSeverity.ERROR,
                ),
            ),
        ),
    )

    assert "Failure: Connection Refused" in detail.body
    assert "Check that Ollama is running." in detail.body
    assert "error: The provider endpoint did not respond." in detail.body

