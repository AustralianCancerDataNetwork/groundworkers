"""The embedding tier's readiness check, and the section that reports it.

`probe_provider` and `reconcile_models` existed and were tested before this, but
nothing in the running console called either: finishing the model and store
journeys turned the Embeddings section green whether or not the store was
reachable or the model registered. These cover the wiring, not the verdicts.
"""

from __future__ import annotations

from pathlib import Path

from oa_configurator import Resolver

from groundworkers.application.setup.embedding_reconciliation import (
    verify_embedding_model,
)
from groundworkers.application.setup.embedding_setup import (
    probe_provider,
    reconcile_models,
)
from groundworkers.application.setup.models import (
    ClassifiedFailure,
    ConfigurationOwnership,
    ConfigurationSnapshot,
    ConfigurationState,
    ConnectionFailureKind,
    DiagnosticSeverity,
    EmbeddingConfiguration,
    EmbeddingStoreSnapshot,
    EmbeddingStoreState,
    ModelReconciliation,
    ProviderCapabilities,
    ProviderSnapshot,
    RegisteredEmbeddingModel,
)
from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
from tests.support.stack_config import build_cdm_stack, build_embedding_stack


def _snapshot(stack, path: Path = Path("/tmp/config.toml")) -> ConfigurationSnapshot:
    stack.bind_loaded_path(path)
    return ConfigurationSnapshot(
        state=ConfigurationState.UNVERIFIED,
        path=path,
        ownership=ConfigurationOwnership(),
        stack=stack,
        revision="test-revision",
    )


def _registered(model_name: str) -> RegisteredEmbeddingModel:
    return RegisteredEmbeddingModel(
        model_name=model_name,
        provider="ollama",
        dimensions=1024,
        metric="cosine",
        index_type="flat",
        has_embeddings=True,
    )


def _store(
    *,
    models: tuple[RegisteredEmbeddingModel, ...] = (),
    reachable: bool = True,
) -> EmbeddingStoreSnapshot:
    return EmbeddingStoreSnapshot(
        state=(
            EmbeddingStoreState.POPULATED if models else EmbeddingStoreState.EMPTY
        )
        if reachable
        else EmbeddingStoreState.UNREACHABLE,
        backend="sqlitevec",
        reachable=reachable,
        models=models,
        failure=(
            None
            if reachable
            else ClassifiedFailure(
                kind=ConnectionFailureKind.REFUSED,
                detail="The store refused the connection.",
                next_action="Start the store and check its connection.",
            )
        ),
    )


def _provider(model_name: str, **overrides: object) -> ProviderSnapshot:
    fields: dict[str, object] = {
        "provider_name": "embedding_provider",
        "provider_kind": "ollama",
        "model_entry_name": "embedding_model",
        "api_base": "http://models.example.test",
        "configured_model": model_name,
        "capabilities": ProviderCapabilities(),
        "reachable": True,
        "encoding_succeeded": True,
        "dimensions": 1024,
    }
    fields.update(overrides)
    return ProviderSnapshot(**fields)  # type: ignore[arg-type]


def _verify(stack, *, store: EmbeddingStoreSnapshot, provider: ProviderSnapshot):
    return verify_embedding_model(
        _snapshot(stack),
        registry_lister=lambda _snapshot: store,
        provider_prober=lambda _model, **_kwargs: provider,
        inventory_discoverer=lambda *_args: (),
    )


def test_a_cdm_only_stack_has_no_embedding_verdict_to_report() -> None:
    """The section already calls this tier unconfigured; a verdict would
    contradict it."""
    assert verify_embedding_model(_snapshot(build_cdm_stack())) is None


def test_a_model_the_store_holds_and_the_provider_serves_reconciles_clean() -> None:
    stack = build_embedding_stack()
    canonical = _canonical(stack)

    result = _verify(
        stack,
        store=_store(models=(_registered(canonical),)),
        provider=_provider(canonical),
    )

    assert result is not None
    assert result.ready_for_population is True
    assert result.model_is_registered is True
    assert result.worst_severity is None


def test_an_unreachable_store_is_a_blocker_rather_than_an_empty_registry() -> None:
    """Without the store snapshot an unreachable store reads as "no models
    registered yet", which is the reassuring reading of the two."""
    stack = build_embedding_stack()
    canonical = _canonical(stack)

    result = _verify(
        stack,
        store=_store(reachable=False),
        provider=_provider(canonical),
    )

    assert result is not None
    unreachable = next(
        item for item in result.diagnostics if item.code == "store_unreachable"
    )
    assert unreachable.severity is DiagnosticSeverity.ERROR
    assert result.ready_for_population is False


def test_an_unreachable_provider_still_reports_the_canonical_name() -> None:
    """The registry holds canonical names, so the reachable and unreachable
    paths must name the same model the same way; this one used to pass the raw
    [models.*] value straight through."""
    stack = build_embedding_stack(model_name="  qwen3-embedding:0.6b  ")

    def fail(_resolved):
        raise RuntimeError("provider is down")

    snapshot = probe_provider(
        Resolver(stack).resolve_model("embedding_model"), backend_factory=fail
    )

    assert snapshot.configured_model == "qwen3-embedding:0.6b"
    assert snapshot.reachable is False


def test_a_model_the_provider_cannot_be_asked_about_is_still_matched() -> None:
    """Follows from the above: an unreachable provider must not make a
    registered model read as unregistered."""
    stack = build_embedding_stack(model_name="  qwen3-embedding:0.6b  ")
    registered = _registered("qwen3-embedding:0.6b")

    result = _verify(
        stack,
        store=_store(models=(registered,)),
        provider=_provider("qwen3-embedding:0.6b", reachable=False, encoding_succeeded=False),
    )

    assert result is not None
    assert result.model_is_registered is True


def test_a_dead_endpoint_is_reported_as_unreachable_not_as_a_bad_model() -> None:
    """Both the capability and the encode verdicts read as claims about the
    model; an endpoint that never answered has made neither."""
    stack = build_embedding_stack()
    canonical = _canonical(stack)

    result = _verify(
        stack,
        store=_store(),
        provider=_provider(
            canonical, reachable=False, encoding_succeeded=False, dimensions=None
        ),
    )

    assert result is not None
    codes = {item.code for item in result.diagnostics}
    assert "provider_unreachable" in codes
    assert "provider_encode_failed" not in codes


def test_an_unchecked_section_is_idle_rather_than_ok() -> None:
    """Two references resolving is not evidence that anything works, which is how
    an empty store and a dead provider both showed green."""
    presenter = EmbeddingsPresenter()

    assert presenter.status(
        database_ready=True, configured=True
    ).name == "IDLE"
    assert presenter.status(
        database_ready=True,
        configured=True,
        reconciliation=_reconciliation(()),
    ).name == "OK"


def test_a_blocked_section_reports_error_and_says_why() -> None:
    presenter = EmbeddingsPresenter()
    reconciliation = _reconciliation(
        (
            ("store_unreachable", DiagnosticSeverity.ERROR, "The store is down."),
        )
    )

    status = presenter.status(
        database_ready=True, configured=True, reconciliation=reconciliation
    )
    view = presenter.landing(
        database_ready=True,
        configuration=_configuration(),
        reconciliation=reconciliation,
    )

    assert status.name == "ERROR"
    assert view.message == "The store is down."
    assert any("The store is down." in row.cells[1] for row in view.rows)


def test_the_unchecked_section_offers_the_check_first() -> None:
    """It is the cheap one, and the one that decides whether populating can
    work at all."""
    view = EmbeddingsPresenter().landing(
        database_ready=True, configuration=_configuration()
    )

    assert view.actions[0].key == "embeddings.check_model"
    assert "Check model" in view.message


def _canonical(stack) -> str:
    from omop_llm import canonical_model_name

    model = stack.models["embedding_model"]
    return canonical_model_name(
        stack.providers[model.provider].provider, model.model
    )


def _reconciliation(
    diagnostics: tuple[tuple[str, DiagnosticSeverity, str], ...],
) -> ModelReconciliation:
    from groundworkers.application.setup.models import ModelDiagnostic

    return ModelReconciliation(
        configured_model="ollama/qwen3-embedding:0.6b",
        registered_models=(),
        provider=None,
        diagnostics=tuple(
            ModelDiagnostic(code=code, severity=severity, message=message)
            for code, severity, message in diagnostics
        ),
    )


def _configuration() -> EmbeddingConfiguration:
    return EmbeddingConfiguration(
        backend="sqlitevec",
        vector_store_name="embedding_store",
        database_name="embedding_db",
        connection_name="embedding_main",
        database_safe_url="sqlite:///embeddings.db",
        provider_name="embedding_provider",
        provider_kind="ollama",
        model_entry_name="embedding_model",
        model_name="qwen3-embedding:0.6b",
        embeddings_supported=True,
        api_base="http://models.example.test",
    )


def test_reconcile_without_a_store_snapshot_says_nothing_about_the_store() -> None:
    """The parameter is optional so existing callers keep their meaning."""
    result = reconcile_models(
        configured_model="ollama/model",
        registered_models=(_registered("ollama/model"),),
        provider=_provider("ollama/model"),
    )

    assert result.store is None
    assert "store_unreachable" not in {item.code for item in result.diagnostics}
