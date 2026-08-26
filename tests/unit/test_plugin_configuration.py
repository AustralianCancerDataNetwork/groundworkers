"""Acceptance coverage for generic plugin configuration without a real plugin."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, ClassVar

from groundskeeping.configurator import ConfigWizardController, MutationOperation
from groundskeeping.contracts import ChoiceStep, FormStep, ReviewStep
from oa_configurator import (
    CDMDatabaseConfig,
    ModelConfig,
    PackageConfigBase,
    RefTo,
    VectorStoreConfig,
    load_stack_config_from_path,
    save_stack_config,
)
from pydantic import Field

from groundworkers.application.setup.plugin_configuration import (
    PackageConfigMutationService,
)
from tests.support.stack_config import build_cdm_stack, build_embedding_stack


class ComparatorRecommenderConfig(PackageConfigBase):
    """The proposed schema; no recommender runtime is needed by these tests."""

    tool_name: ClassVar[str] = "comparator_recommender"

    comparator_db: Annotated[str, RefTo(CDMDatabaseConfig)] = Field(
        default="cdm_db"
    )
    embedding_model_name: Annotated[str | None, RefTo(ModelConfig)] = Field(
        default=None
    )
    vector_store_name: Annotated[str | None, RefTo(VectorStoreConfig)] = Field(
        default=None
    )
    min_databases_default: int = Field(default=2, ge=1)


def _path(tmp_path: Path, *, embeddings: bool) -> Path:
    path = tmp_path / "config.toml"
    stack = build_embedding_stack() if embeddings else build_cdm_stack()
    save_stack_config(stack, path)
    return path


def _controller(path: Path):
    service = PackageConfigMutationService(path, ComparatorRecommenderConfig)
    workflow = service.workflow(MutationOperation.UPDATE)
    return ConfigWizardController(workflow, service), workflow


def _submit_step(controller, workflow, snapshot, answers):
    step = next(item for item in workflow.steps if item.key == snapshot.step.key)
    values = {}
    for key in step.field_keys:
        if key in answers:
            values[key] = answers[key]
            continue
        field = next(
            field
            for field in getattr(snapshot.step, "fields", ())
            if field.key == key
        )
        values[key] = field.default
    return controller.submit(values).snapshot


def _drive(controller, workflow, answers):
    snapshot = controller.start()
    while not isinstance(snapshot.step, ReviewStep):
        if isinstance(snapshot.step, ChoiceStep):
            step = next(item for item in workflow.steps if item.key == snapshot.step.key)
            key = step.field_keys[0]
            snapshot = controller.submit({key: answers[key]}).snapshot
        elif isinstance(snapshot.step, FormStep):
            snapshot = _submit_step(controller, workflow, snapshot, answers)
        else:  # pragma: no cover - the generic controller has only these steps
            raise AssertionError(type(snapshot.step))
    return snapshot


def test_existing_references_and_pydantic_constraint_run_through_controller(
    tmp_path: Path,
) -> None:
    path = _path(tmp_path, embeddings=True)
    controller, workflow = _controller(path)
    snapshot = controller.start()
    snapshot = controller.submit({"comparator_db": "cdm_db"}).snapshot
    snapshot = controller.submit({"embedding_model_name": "embedding_model"}).snapshot
    snapshot = controller.submit({"vector_store_name": "embedding_store"}).snapshot

    invalid = controller.submit({"min_databases_default": 0})
    assert invalid.issues
    assert invalid.snapshot.step.key == snapshot.step.key

    review = controller.submit({"min_databases_default": 3}).snapshot
    assert isinstance(review.step, ReviewStep)
    assert review.can_apply
    assert controller.apply().applied

    saved = load_stack_config_from_path(path)
    config = ComparatorRecommenderConfig.validate_candidate(saved)
    assert config.comparator_db == "cdm_db"
    assert config.embedding_model_name == "embedding_model"
    assert config.vector_store_name == "embedding_store"
    assert config.min_databases_default == 3
    assert workflow.target.key == "gw_plugin-comparator_recommender"


def test_recursive_reference_creation_writes_a_complete_oa_stack(tmp_path: Path) -> None:
    path = _path(tmp_path, embeddings=False)
    controller, workflow = _controller(path)
    create = "__create_new__"
    answers = {
        "comparator_db": "cdm_db",
        "embedding_model_name": create,
        "embedding_model_name::name": "plugin_embedding_model",
        "embedding_model_name::provider": create,
        "embedding_model_name::provider::name": "plugin_provider",
        "embedding_model_name::provider::provider": "ollama",
        "embedding_model_name::provider::base_url": "http://localhost:11434",
        "embedding_model_name::provider::api_key": "plugin-secret-canary",
        "embedding_model_name::model": "qwen3-embedding:0.6b",
        "embedding_model_name::embeddings": True,
        "vector_store_name": create,
        "vector_store_name::name": "plugin_vectors",
        "vector_store_name::database": create,
        "vector_store_name::database::name": "plugin_vector_db",
        "vector_store_name::database::connection": create,
        "vector_store_name::database::connection::name": "plugin_vector_connection",
        "vector_store_name::database::connection::dialect": "sqlite",
        "vector_store_name::database::connection::database_name": str(
            tmp_path / "plugin-vectors.db"
        ),
        "vector_store_name::database::schema_name": "main",
        "vector_store_name::backend_type": "sqlitevec",
        "min_databases_default": 2,
    }

    review = _drive(controller, workflow, answers)
    assert review.can_apply
    assert "plugin-secret-canary" not in repr(review)
    assert controller.apply().applied

    saved = load_stack_config_from_path(path)
    config = ComparatorRecommenderConfig.validate_candidate(saved)
    assert config.embedding_model_name == "plugin_embedding_model"
    assert config.vector_store_name == "plugin_vectors"
    assert saved.models["plugin_embedding_model"].provider == "plugin_provider"
    assert saved.providers["plugin_provider"].api_key == "plugin-secret-canary"
    assert saved.vector_stores["plugin_vectors"].database == "plugin_vector_db"
    assert saved.databases["plugin_vector_db"].connection == "plugin_vector_connection"


def test_concurrent_write_after_review_conflicts(tmp_path: Path) -> None:
    path = _path(tmp_path, embeddings=True)
    controller, workflow = _controller(path)
    review = _drive(
        controller,
        workflow,
        {
            "comparator_db": "cdm_db",
            "embedding_model_name": "embedding_model",
            "vector_store_name": "embedding_store",
            "min_databases_default": 2,
        },
    )
    assert review.can_apply

    changed = load_stack_config_from_path(path)
    changed.tools["external_writer"] = {"changed": True}
    save_stack_config(changed, path)

    assert controller.apply().status.value == "conflicted"
    assert "comparator_recommender" not in load_stack_config_from_path(path).tools


def test_attempted_save_failure_returns_failed(tmp_path: Path, monkeypatch) -> None:
    path = _path(tmp_path, embeddings=True)
    controller, workflow = _controller(path)
    review = _drive(
        controller,
        workflow,
        {
            "comparator_db": "cdm_db",
            "embedding_model_name": "embedding_model",
            "vector_store_name": "embedding_store",
            "min_databases_default": 2,
        },
    )
    assert review.can_apply

    def fail_save(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(
        "groundworkers.application.setup.plugin_configuration.save_configuration",
        fail_save,
    )

    assert controller.apply().status.value == "failed"
