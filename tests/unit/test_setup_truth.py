from __future__ import annotations

from pathlib import Path

from groundskeeping.configurator import MutationOperation
from oa_configurator import save_stack_config

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.configuration_provider import (
    CDM_SETUP_TARGET,
    GroundworkersConfigMutationService,
)
from groundworkers.application.setup.embedding_capability import (
    embedding_capability_state,
)
from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    CoverageScope,
    CoverageSnapshot,
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
    EmbeddingIndexSnapshot,
    EmbeddingStoreSnapshot,
    EmbeddingStoreState,
    ModelReconciliation,
    OwnershipMode,
    ProviderCapabilities,
    ProviderSnapshot,
    RegisteredEmbeddingModel,
    VocabularyCoverage,
)
from groundworkers.tui.presenters.overview import OverviewPresenter
from tests.support.stack_config import build_cdm_stack


def _configuration() -> EmbeddingConfiguration:
    return EmbeddingConfiguration(
        backend="pgvector",
        vector_store_name="embeddings",
        database_name="emb_db",
        connection_name="postgres",
        database_safe_url="postgresql://db/emb",
        provider_name="provider",
        provider_kind="ollama",
        model_entry_name="embedding_model",
        model_name="embed:v1",
        embeddings_supported=True,
        api_base="http://models/v1",
    )


def _reconciliation(*, registered: bool = True) -> ModelReconciliation:
    model = RegisteredEmbeddingModel(
        model_name="embed:v1",
        provider="ollama",
        dimensions=384,
        metric="cosine",
        index_type="flat",
        has_embeddings=registered,
    )
    return ModelReconciliation(
        configured_model="embed:v1",
        registered_models=(model,) if registered else (),
        provider=ProviderSnapshot(
            provider_name="provider",
            provider_kind="ollama",
            model_entry_name="embedding_model",
            api_base="http://models/v1",
            configured_model="embed:v1",
            capabilities=ProviderCapabilities(),
            reachable=True,
            encoding_succeeded=True,
            dimensions=384,
        ),
        diagnostics=(),
        store=EmbeddingStoreSnapshot(
            state=EmbeddingStoreState.POPULATED,
            backend="pgvector",
            reachable=True,
            models=(model,) if registered else (),
        ),
    )


def _coverage(*, initialized: bool, pending: int) -> EmbeddingCoverageReport:
    configuration = _configuration()
    coverage = CoverageSnapshot(
        scope=CoverageScope(
            model_name="embed:v1",
            metric="cosine",
            vocabularies=("SNOMED",),
        ),
        available=True,
        rows=(VocabularyCoverage("SNOMED", 10, 10 - pending, pending, 100 - pending * 10),),
        eligible_total=10,
        embedded_total=10 - pending,
        pending_total=pending,
        metadata={"store_initialized": initialized},
    )
    return EmbeddingCoverageReport(
        configuration=configuration,
        coverage=coverage,
        index=EmbeddingIndexSnapshot(
            model_name="embed:v1",
            registered=True,
            registry_index_type="flat",
        ),
    )


def test_embedding_capability_rejects_false_green_and_gates_population() -> None:
    uninitialized = embedding_capability_state(
        _configuration(),
        _coverage(initialized=False, pending=5),
        _reconciliation(),
    )
    populatable = embedding_capability_state(
        _configuration(),
        _coverage(initialized=True, pending=5),
        _reconciliation(registered=False),
    )
    ready = embedding_capability_state(
        _configuration(),
        _coverage(initialized=True, pending=0),
        _reconciliation(),
    )

    assert uninitialized.ready is False
    assert uninitialized.can_populate is False
    assert populatable.can_populate is True
    assert populatable.ready is False
    assert ready.ready is True


def test_fresh_cdm_fields_are_postgresql_first_and_memory_sqlite_is_rejected(
    tmp_path: Path,
) -> None:
    service = GroundworkersConfigMutationService(tmp_path / "config.toml")
    draft = service.begin(CDM_SETUP_TARGET, MutationOperation.CREATE)
    fields = {field.key: field for field in service.fields(draft)}

    assert fields["dialect"].default == "postgresql+psycopg"
    assert fields["host"].default == "localhost"
    assert fields["port"].default == 5432
    assert fields["database_name"].default is None
    assert fields["schema_name"].default == "public"

    service.submit(
        draft,
        "connection",
        {"connection_name": "cdm_main", "dialect": "sqlite"},
    )
    rejected = service.submit(
        draft,
        "database",
        {"database_name": ":memory:"},
    )
    assert rejected.accepted is False
    assert "in-memory" in rejected.issues[0].message


def test_read_only_overview_disables_configuration_mutations(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)
    ownership = ConfigurationOwnership(
        mode=OwnershipMode.DERIVED_READ_ONLY,
        source_label="Container configuration",
        guidance="Change the deployment source.",
    )
    snapshot = load_configuration(config_path=path, ownership=ownership)

    view = OverviewPresenter().landing(
        snapshot,
        connections=(),
        embedding_coverage=None,
        llm_result=None,
        graph_ready=False,
        integration_ready=False,
    )
    actions = {action.key: action for action in view.actions}

    assert actions["database.configure"].disabled is True
    assert actions["embeddings.configure_model"].disabled is True
    assert actions["llm_provider.configure"].disabled is True
    assert "Container configuration" in view.message
