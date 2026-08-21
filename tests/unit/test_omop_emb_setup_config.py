from __future__ import annotations

from pathlib import Path

from groundskeeping.configurator import ConfigWizardController, MutationOperation
from oa_configurator import load_stack_config_from_path, save_stack_config

from groundworkers.application.setup.configuration_provider import (
    STORE_LOCATION_CDM,
    GroundworkersConfigMutationService,
    _align_cdm_cli_configs,
    _align_omop_emb_config,
    cdm_setup_workflow,
    vector_store_setup_workflow,
)
from groundworkers.config import GroundworkersConfig
from tests.support.stack_config import build_cdm_stack, build_embedding_stack


def test_cdm_cli_packages_share_groundworkers_database_reference() -> None:
    stack = build_cdm_stack(database_name="warehouse_cdm")

    aligned = _align_cdm_cli_configs(stack)

    assert aligned.tools["omop_alchemy"] == {"cdm_db": "warehouse_cdm"}
    assert aligned.tools["omop_graph"] == {
        "cdm_db": "warehouse_cdm",
        "max_depth": 6,
        "max_paths": 20,
    }


def test_existing_cdm_cli_references_are_reconciled() -> None:
    stack = build_cdm_stack()
    stack.tools["omop_alchemy"] = {"cdm_db": "old_cdm"}
    stack.tools["omop_graph"] = {"cdm_db": "old_cdm", "max_depth": 4}

    aligned = _align_cdm_cli_configs(stack)

    assert aligned.tools["omop_alchemy"]["cdm_db"] == "cdm_db"
    assert aligned.tools["omop_graph"] == {
        "cdm_db": "cdm_db",
        "max_depth": 4,
        "max_paths": 20,
    }


def test_cdm_setup_saves_companion_cli_sections_with_custom_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)
    sqlite_path = tmp_path / "warehouse.db"
    sqlite_path.touch()
    service = GroundworkersConfigMutationService(path)
    controller = ConfigWizardController(
        cdm_setup_workflow(MutationOperation.UPDATE), service
    )
    controller.start()
    controller.submit({"connection_name": "warehouse", "dialect": "sqlite"})
    controller.submit({"database_name": str(sqlite_path)})
    review = controller.submit(
        {
            "cdm_db_name": "warehouse_cdm",
            "schema_name": "main",
            "vocab_schema": "main",
            "results_schema": None,
        }
    ).snapshot

    assert {
        change.field for change in review.step.review.changes
    } >= {
        "tools.groundworkers.cdm_db",
        "tools.omop_alchemy.cdm_db",
        "tools.omop_graph.cdm_db",
    }
    assert controller.apply().status.value == "applied"

    saved = load_stack_config_from_path(path)
    assert saved.tools["omop_alchemy"]["cdm_db"] == "warehouse_cdm"
    assert saved.tools["omop_graph"]["cdm_db"] == "warehouse_cdm"


def test_complete_embedding_tier_configures_omop_emb_with_shared_references() -> None:
    stack = build_embedding_stack()

    aligned = _align_omop_emb_config(stack)

    groundworkers = GroundworkersConfig.validate_candidate(aligned)
    assert aligned.tools["omop_emb"] == {
        "cdm_db": groundworkers.cdm_db,
        "embedding_model_name": groundworkers.embedding_model_name,
        "vector_store_name": groundworkers.vector_store_name,
    }


def test_partial_embedding_tier_does_not_create_invalid_omop_emb_section() -> None:
    stack = build_cdm_stack(
        groundworkers={"embedding_model_name": None, "vector_store_name": None}
    )

    aligned = _align_omop_emb_config(stack)

    assert aligned is stack
    assert "omop_emb" not in aligned.tools


def test_existing_omop_emb_references_are_reconciled_to_groundworkers() -> None:
    stack = build_embedding_stack()
    stack.tools["omop_emb"] = {
        "cdm_db": "old_cdm",
        "embedding_model_name": "old_model",
        "vector_store_name": "old_store",
    }

    aligned = _align_omop_emb_config(stack)

    assert aligned.tools["omop_emb"] == {
        "cdm_db": "cdm_db",
        "embedding_model_name": "embedding_model",
        "vector_store_name": "embedding_store",
    }


def test_vector_store_setup_completes_and_saves_both_package_sections(
    tmp_path: Path,
) -> None:
    stack = build_embedding_stack(vector_store_name="embeddings")
    del stack.vector_stores["embeddings"]
    stack.tools["groundworkers"].pop("vector_store_name")
    path = tmp_path / "config.toml"
    save_stack_config(stack, path)
    service = GroundworkersConfigMutationService(path)
    controller = ConfigWizardController(
        vector_store_setup_workflow(MutationOperation.CREATE), service
    )
    controller.start()
    controller.submit({"store_location": STORE_LOCATION_CDM})
    review = controller.submit(
        {
            "store_entry_name": "embeddings",
            "backend_type": "sqlitevec",
            "faiss_cache_dir": None,
        }
    ).snapshot

    assert {
        change.field for change in review.step.review.changes
    } >= {
        "tools.groundworkers.vector_store_name",
        "tools.omop_emb.cdm_db",
        "tools.omop_emb.embedding_model_name",
        "tools.omop_emb.vector_store_name",
    }
    assert controller.apply().status.value == "applied"

    saved = load_stack_config_from_path(path)
    assert saved.tools["omop_emb"] == {
        "cdm_db": "cdm_db",
        "embedding_model_name": "embedding_model",
        "vector_store_name": "embeddings",
    }
