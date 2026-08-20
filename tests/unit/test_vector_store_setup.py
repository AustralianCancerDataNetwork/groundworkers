from __future__ import annotations

from pathlib import Path

from groundskeeping.configurator import ConfigWizardController, MutationOperation
from oa_configurator import load_stack_config_from_path
from oa_configurator.io import save_stack_config

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.configuration_provider import (
    GroundworkersConfigMutationService,
    vector_store_setup_workflow,
)
from groundworkers.application.setup.databases import resolve_database_targets
from groundworkers.config import GroundworkersConfig
from groundworkers.tui.presenters.database import (
    EMBEDDING_TARGET_KEY,
    DatabasePresenter,
)
from tests.support.stack_config import build_cdm_stack


def _stack_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)
    return path


def _drive(path: Path, **overrides: object):
    service = GroundworkersConfigMutationService(config_path=path)
    controller = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.CREATE), service
    )
    controller.start()
    controller.submit(
        {
            "store_entry_name": overrides.get("store_entry_name", "embeddings"),
            "backend_type": overrides.get("backend_type", "sqlitevec"),
            "faiss_cache_dir": overrides.get("faiss_cache_dir"),
        }
    )
    controller.submit(
        {
            "store_database_name": overrides.get("store_database_name", "embedding_db"),
            "store_connection_name": overrides.get("store_connection_name", "cdm_main"),
            "store_schema_name": overrides.get("store_schema_name", "public"),
        }
    )
    return controller


def test_the_journey_creates_the_store_its_database_and_the_reference(
    tmp_path: Path,
) -> None:
    """The embedding tier needs vector_store_name as well as embedding_model_name,
    and nothing in the console wrote it before."""
    path = _stack_path(tmp_path)

    assert _drive(path).apply().status.value == "applied"

    stack = load_stack_config_from_path(path)
    config = GroundworkersConfig.validate_candidate(stack)
    assert config.vector_store_name == "embeddings"
    assert stack.vector_stores["embeddings"].database == "embedding_db"
    assert stack.databases["embedding_db"].kind == "generic"


def test_the_backend_choice_is_carried_through(tmp_path: Path) -> None:
    path = _stack_path(tmp_path)

    _drive(path, backend_type="pgvector").apply()

    stack = load_stack_config_from_path(path)
    assert stack.vector_stores["embeddings"].backend_type == "pgvector"


def test_a_blank_faiss_cache_is_left_unset(tmp_path: Path) -> None:
    """Optional, and meaningless without the embedding-faiss extra."""
    path = _stack_path(tmp_path)

    _drive(path, faiss_cache_dir="").apply()

    stack = load_stack_config_from_path(path)
    assert stack.vector_stores["embeddings"].faiss_cache_dir is None


def test_the_store_reuses_a_named_connection_rather_than_defining_one(
    tmp_path: Path,
) -> None:
    """Creating connections is the CDM journey's job; this one references."""
    path = _stack_path(tmp_path)

    _drive(path).apply()

    stack = load_stack_config_from_path(path)
    assert set(stack.connections) == {"cdm_main"}
    assert stack.databases["embedding_db"].connection == "cdm_main"


def test_the_configured_store_then_appears_as_a_database_target(
    tmp_path: Path,
) -> None:
    """Once configured it is a real connection target, checked like the others."""
    path = _stack_path(tmp_path)
    _drive(path).apply()

    targets = resolve_database_targets(load_configuration(config_path=path))

    assert EMBEDDING_TARGET_KEY in {target.key for target in targets}


def test_an_unconfigured_store_still_shows_a_row(tmp_path: Path) -> None:
    """Otherwise there is nothing to select, and so no way to configure one."""
    snapshot = load_configuration(config_path=_stack_path(tmp_path))

    view = DatabasePresenter().landing(snapshot, resolve_database_targets(snapshot), ())

    row = next(row for row in view.rows if row.key == EMBEDDING_TARGET_KEY)
    assert "Not configured" in row.cells
