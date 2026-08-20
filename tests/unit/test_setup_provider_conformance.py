"""Groundskeeping's provider contract suite, run against the real provider.

`GroundworkersConfigMutationService` is the only configuration writer, so it has to
satisfy the portable lifecycle contract for every target it supports. Running
groundskeeping's own suite here means the contract is enforced on every test run
rather than verified by hand at review time.

The suite covers capability discovery, begin/fields/stage/plan/apply, single-use
apply tokens, diff projection symmetry, cancellation, validation issue locations,
stale-revision conflict, typed refusal of an unsupported operation, and the absence
of a secret canary from every returned object.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("groundskeeping")  # setup write flows live behind the `tui` extra

from groundskeeping.configurator import (
    MutationConformanceHooks,
    MutationOperation,
    assert_mutation_service_conformance,
)
from oa_configurator import save_stack_config

from groundworkers.application.setup.configuration_provider import (
    CDM_SETUP_TARGET,
    LLM_SETUP_TARGET,
    MODEL_SETUP_TARGET,
    VECTOR_STORE_SETUP_TARGET,
    GroundworkersConfigMutationService,
)
from tests.support.stack_config import build_cdm_stack

# A value that must never appear in any object the provider hands back.
CANARY = "conformance-canary-secret"

# Each case reaches a complete, valid candidate for one target.
CASES = {
    "cdm": (
        CDM_SETUP_TARGET,
        MutationOperation.UPDATE,
        (
            ("connection", {"connection_name": "warehouse", "dialect": "sqlite"}),
            ("database", {"database_name": "/tmp/conformance-warehouse.db"}),
            (
                "cdm",
                {
                    "cdm_db_name": "warehouse_cdm",
                    "schema_name": "main",
                    "vocab_schema": "main",
                },
            ),
        ),
    ),
    "embedding_model": (
        MODEL_SETUP_TARGET,
        MutationOperation.CREATE,
        (
            (
                "provider",
                {
                    "provider_name": "embedding_provider",
                    "provider_kind": "ollama",
                    "base_url": "http://localhost:11434",
                    "api_key": CANARY,
                },
            ),
            ("model", {"model_entry_name": "embedding_model", "model_choice": "m1:v1"}),
        ),
    ),
    "vector_store": (
        VECTOR_STORE_SETUP_TARGET,
        MutationOperation.CREATE,
        (
            (
                "store",
                {
                    "store_entry_name": "embeddings",
                    "backend_type": "sqlitevec",
                    "faiss_cache_dir": None,
                },
            ),
            (
                "database",
                {
                    "store_database_name": "embedding_db",
                    "store_connection_name": "cdm_main",
                    "store_schema_name": "public",
                },
            ),
        ),
    ),
    "chat": (
        LLM_SETUP_TARGET,
        MutationOperation.CREATE,
        (
            (
                "provider",
                {
                    "llm_provider_name": "chat_provider",
                    "llm_provider_kind": "ollama",
                    "llm_base_url": "http://localhost:11434/v1",
                    "llm_api_key": CANARY,
                },
            ),
            ("model", {"llm_model_entry_name": "chat_model", "llm_model_choice": "m1:v1"}),
        ),
    ),
}


def _factory(tmp_path: Path, name: str):
    """Return a fresh provider over an isolated config file per call.

    The suite requires isolated instances: it stages the same submissions several
    times and must not see state carried between them.
    """
    counter = {"n": 0}

    def make() -> GroundworkersConfigMutationService:
        counter["n"] += 1
        path = tmp_path / f"{name}-{counter['n']}.toml"
        save_stack_config(build_cdm_stack(), path)
        return GroundworkersConfigMutationService(
            path,
            model_discoverer=lambda *_args: ("m1:v1", "m2:v1"),
        )

    return make


@pytest.mark.parametrize("name", sorted(CASES))
def test_provider_satisfies_the_mutation_lifecycle_contract(
    tmp_path: Path, name: str
) -> None:
    target, operation, submissions = CASES[name]

    assert_mutation_service_conformance(
        _factory(tmp_path, name),
        target,
        operation,
        submissions,
        secret_canary=CANARY,
    )


@pytest.mark.parametrize("name", ["embedding_model", "vector_store", "chat"])
def test_provider_satisfies_the_hooked_contract(tmp_path: Path, name: str) -> None:
    """The fault-injection half: validation, conflict, and typed refusal."""
    target, operation, submissions = CASES[name]
    invalid_values = {
        "embedding_model": ({"provider_name": ""}, frozenset({"provider_name"})),
        "vector_store": (
            {"store_entry_name": "", "backend_type": ""},
            frozenset({"store_entry_name", "backend_type"}),
        ),
        "chat": (
            # Valid entry name, so only the two genuinely invalid fields report.
            {
                "llm_provider_name": "chat_provider",
                "llm_provider_kind": "unsupported",
                "llm_base_url": "",
            },
            frozenset({"llm_provider_kind", "llm_base_url"}),
        ),
    }
    values, expected_fields = invalid_values[name]

    # Each journey names its own first step, so take it from the case rather
    # than assuming every journey starts with a provider.
    first_step_key = submissions[0][0]

    def invalid_submission(service, draft):
        return service.submit(draft, first_step_key, values)

    def advance_revision(service):
        # Another operator writes the file after this provider prepared its plan.
        save_stack_config(build_cdm_stack(schema_name="elsewhere"), service._path)

    assert_mutation_service_conformance(
        _factory(tmp_path, f"{name}-hooked"),
        target,
        operation,
        submissions,
        hooks=MutationConformanceHooks(
            invalid_submission=invalid_submission,
            expected_invalid_fields=expected_fields,
            advance_revision=advance_revision,
            # Nothing is configured yet, so the opposite operation is refused.
            unsupported_operation=MutationOperation.UPDATE,
        ),
        secret_canary=CANARY,
    )
