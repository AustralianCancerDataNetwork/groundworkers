from __future__ import annotations

from pathlib import Path

from groundskeeping.configurator import ConfigWizardController, MutationOperation
from groundskeeping.contracts.wizards import FormStep
from oa_configurator.io import save_stack_config

from groundworkers.application.setup.configuration_provider import (
    GroundworkersConfigMutationService,
    model_setup_workflow,
)
from groundworkers.application.setup.models import (
    EmbeddingStoreSnapshot,
    EmbeddingStoreState,
    RegisteredEmbeddingModel,
)
from tests.support.stack_config import build_cdm_stack

PROVIDER_ANSWERS = {
    "provider_name": "local_models",
    "provider_kind": "ollama",
    "base_url": "http://models.example/v1",
    "api_key": None,
}


def _registered(*names: str) -> EmbeddingStoreSnapshot:
    return EmbeddingStoreSnapshot(
        state=EmbeddingStoreState.POPULATED,
        backend="pgvector",
        reachable=True,
        models=tuple(
            RegisteredEmbeddingModel(
                model_name=name,
                provider="ollama",
                dimensions=1024,
                metric="cosine",
                index_type="hnsw",
                has_embeddings=True,
            )
            for name in names
        ),
    )


def _controller(tmp_path: Path, store: EmbeddingStoreSnapshot):
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)
    service = GroundworkersConfigMutationService(
        path,
        model_discoverer=lambda *_: ("provider-a:v1", "provider-b:v1"),
        registry_lister=lambda _snapshot: store,
    )
    controller = ConfigWizardController(
        model_setup_workflow(MutationOperation.CREATE), service
    )
    controller.start()
    return controller


def _field(snapshot, key):
    assert isinstance(snapshot.step, FormStep)
    return next(field for field in snapshot.step.fields if field.key == key)


def test_the_registry_step_lists_what_the_store_holds(tmp_path: Path) -> None:
    """The provider lists what it could embed with; the registry lists what has
    been embedded. Grounding needs the second, so the step shows it."""
    controller = _controller(tmp_path, _registered("arctic-embed2:v1", "bge-m3"))

    registry = controller.submit(PROVIDER_ANSWERS).snapshot

    assert registry.step.key == "registry"
    source = _field(registry, "model_source")
    assert "arctic-embed2:v1" in source.help
    assert "1024d" in source.help
    assert source.default == "registered"


def test_choosing_registered_offers_the_stores_models(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _registered("arctic-embed2:v1", "bge-m3"))
    controller.submit(PROVIDER_ANSWERS)

    model = controller.submit({"model_source": "registered"}).snapshot

    choice = _field(model, "model_choice")
    assert tuple(option.value for option in choice.choices) == (
        "arctic-embed2:v1",
        "bge-m3",
    )
    assert "vectors already exist" in choice.help


def test_choosing_new_offers_the_providers_models(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _registered("arctic-embed2:v1"))
    controller.submit(PROVIDER_ANSWERS)

    model = controller.submit({"model_source": "new"}).snapshot

    choice = _field(model, "model_choice")
    assert tuple(option.value for option in choice.choices) == (
        "provider-a:v1",
        "provider-b:v1",
    )
    assert "Populate embeddings after saving" in choice.help


def test_an_empty_registry_forces_registering_a_new_model(tmp_path: Path) -> None:
    """Offering 'use a registered model' with none registered would be a dead end."""
    empty = EmbeddingStoreSnapshot(
        state=EmbeddingStoreState.EMPTY, backend="pgvector", reachable=True
    )
    controller = _controller(tmp_path, empty)

    registry = controller.submit(PROVIDER_ANSWERS).snapshot

    source = _field(registry, "model_source")
    assert tuple(option.value for option in source.choices) == ("new",)
    assert source.disabled is True
    assert "no registered models yet" in source.help


def test_an_unconfigured_store_says_so(tmp_path: Path) -> None:
    unconfigured = EmbeddingStoreSnapshot(
        state=EmbeddingStoreState.UNCONFIGURED, backend=None, reachable=False
    )
    controller = _controller(tmp_path, unconfigured)

    registry = controller.submit(PROVIDER_ANSWERS).snapshot

    assert "No embedding store is configured" in _field(registry, "model_source").help


def test_an_unreachable_store_does_not_claim_the_registry_is_empty(
    tmp_path: Path,
) -> None:
    """Unknown is not the same as empty, and the difference changes what to do."""
    unreachable = EmbeddingStoreSnapshot(
        state=EmbeddingStoreState.UNREACHABLE, backend="pgvector", reachable=False
    )
    controller = _controller(tmp_path, unreachable)

    registry = controller.submit(PROVIDER_ANSWERS).snapshot

    assert "could not be reached" in _field(registry, "model_source").help


def test_a_registered_model_still_writes_the_same_config_entry(tmp_path: Path) -> None:
    """The registry decides which name is chosen, not what gets written."""
    controller = _controller(tmp_path, _registered("arctic-embed2:v1"))
    controller.submit(PROVIDER_ANSWERS)
    controller.submit({"model_source": "registered"})
    controller.submit(
        {"model_entry_name": "embedding_model", "model_choice": "arctic-embed2:v1"}
    )
    controller.submit({"prefix_convention": "query_only"})

    assert controller.apply().status.value == "applied"
