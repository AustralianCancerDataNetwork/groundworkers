from __future__ import annotations

from typing import cast

import pytest
from oa_configurator import (
    CDMDatabaseConfig,
    ConfigurationError,
    GenericDatabaseConfig,
)

from groundworkers.config import GroundworkersConfig
from tests.support import (
    build_cdm_stack,
    build_embedding_stack,
    build_invalid_reference_stack,
)
from tests.support.stack_config import EmbeddingBackend, InvalidReference


def test_cdm_fixture_is_a_valid_groundworkers_candidate() -> None:
    stack = build_cdm_stack()

    resolved = GroundworkersConfig.validate_candidate(stack)

    assert resolved.cdm_db == "cdm_db"
    assert isinstance(stack.databases["cdm_db"], CDMDatabaseConfig)


@pytest.mark.parametrize("backend", ("sqlitevec", "pgvector"))
def test_embedding_fixture_uses_current_named_sections(backend: str) -> None:
    stack = build_embedding_stack(cast(EmbeddingBackend, backend))

    resolved = GroundworkersConfig.validate_candidate(stack)

    assert resolved.embedding_model_name == "embedding_model"
    assert resolved.vector_store_name == "embedding_store"
    assert isinstance(stack.databases["embedding_db"], GenericDatabaseConfig)
    assert stack.vector_stores["embedding_store"].backend_type == backend


@pytest.mark.parametrize(
    "issue",
    (
        "missing_cdm",
        "wrong_cdm_kind",
        "missing_model",
        "missing_vector_store",
    ),
)
def test_invalid_reference_fixtures_fail_package_validation(issue: str) -> None:
    stack = build_invalid_reference_stack(cast(InvalidReference, issue))

    with pytest.raises(ConfigurationError):
        GroundworkersConfig.validate_candidate(stack)
