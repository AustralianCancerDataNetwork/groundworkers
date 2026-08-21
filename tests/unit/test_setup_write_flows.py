"""R5 — every Groundworkers setup write goal runs through one generic flow.

Covers the three supported journeys (CDM database, embedding model, chat model)
driven through Groundskeeping's reusable ``ConfigWizardController`` over the single
``GroundworkersConfigMutationService``. There is no second writer and no
Groundworkers-specific wizard controller left to test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("groundskeeping")  # setup write flows live behind the `tui` extra

from groundskeeping.configurator import (
    ConfigWizardController,
    MutationOperation,
)
from oa_configurator import load_stack_config_from_path, save_stack_config

from groundworkers.application.setup.configuration_provider import (
    CDM_SETUP_TARGET,
    LLM_SETUP_TARGET,
    MODEL_SETUP_TARGET,
    GroundworkersConfigMutationService,
    cdm_setup_workflow,
    llm_setup_workflow,
    model_setup_workflow,
)
from groundworkers.config import GroundworkersConfig
from tests.support.stack_config import build_cdm_stack

DISCOVERED_MODELS = ("first-model:v1", "second-model:v1")


def _stack_path(tmp_path: Path, stack=None) -> Path:
    path = tmp_path / "config.toml"
    save_stack_config(stack if stack is not None else build_cdm_stack(), path)
    return path


def _service(path: Path, *, discoverer=None, **kwargs):
    return GroundworkersConfigMutationService(
        path,
        model_discoverer=discoverer or (lambda *_args: DISCOVERED_MODELS),
        **kwargs,
    )


def _controller(path: Path, target, workflow, *, discoverer=None, **kwargs):
    service = _service(path, discoverer=discoverer, **kwargs)
    operation = (
        MutationOperation.UPDATE
        if service.capabilities(target, MutationOperation.UPDATE).supported
        else MutationOperation.CREATE
    )
    return ConfigWizardController(workflow(operation), service), operation


def _drive_llm_to_review(controller, *, api_key: str | None = "provider-secret"):
    controller.start()
    controller.submit(
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": api_key,
        }
    )
    return controller.submit({"llm_model_entry_name": "chat_model", "llm_model_choice": "second-model:v1"})


# ---------------------------------------------------------------------------
# One write path per goal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "workflow"),
    [
        (CDM_SETUP_TARGET, cdm_setup_workflow),
        (MODEL_SETUP_TARGET, model_setup_workflow),
        (LLM_SETUP_TARGET, llm_setup_workflow),
    ],
)
def test_every_supported_goal_is_driven_by_the_generic_controller(
    tmp_path: Path, target, workflow
) -> None:
    controller, _ = _controller(_stack_path(tmp_path), target, workflow)

    snapshot = controller.start()

    # The controller — not a Groundworkers wizard — owns the journey.
    assert isinstance(controller, ConfigWizardController)
    assert snapshot.step is not None


# ---------------------------------------------------------------------------
# Create and update
# ---------------------------------------------------------------------------


def test_chat_setup_creates_then_updates_the_same_target(tmp_path: Path) -> None:
    path = _stack_path(tmp_path)

    controller, operation = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    assert operation is MutationOperation.CREATE
    _drive_llm_to_review(controller)
    assert controller.apply().status.value == "applied"

    stack = load_stack_config_from_path(path)
    saved = GroundworkersConfig.validate_candidate(stack)
    assert saved.llm_model_name == "chat_model"
    assert stack.models["chat_model"].model == "second-model:v1"
    assert stack.providers["chat_provider"].provider == "ollama"

    # A second pass over an existing entry is an update, not a duplicate create.
    controller, operation = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    assert operation is MutationOperation.UPDATE
    controller.start()
    controller.submit(
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": None,
        }
    )
    controller.submit({"llm_model_entry_name": "chat_model", "llm_model_choice": "first-model:v1"})
    assert controller.apply().status.value == "applied"

    updated_stack = load_stack_config_from_path(path)
    updated = GroundworkersConfig.validate_candidate(updated_stack)
    assert updated.llm_model_name == "chat_model"
    assert updated_stack.models["chat_model"].model == "first-model:v1"


def test_blank_api_key_on_update_preserves_the_stored_credential(
    tmp_path: Path,
) -> None:
    path = _stack_path(tmp_path)
    controller, _ = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    _drive_llm_to_review(controller, api_key="original-secret")
    controller.apply()

    controller, _ = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    controller.start()
    controller.submit(
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": "",
        }
    )
    controller.submit({"llm_model_entry_name": "chat_model", "llm_model_choice": "first-model:v1"})
    controller.apply()

    stack = load_stack_config_from_path(path)
    assert stack.providers["chat_provider"].api_key == "original-secret"


def test_cdm_setup_creates_connection_and_database_entries(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), path)

    controller, _ = _controller(path, CDM_SETUP_TARGET, cdm_setup_workflow)
    sqlite_path = tmp_path / "warehouse.db"
    sqlite_path.touch()
    controller.start()
    controller.submit({"connection_name": "warehouse", "dialect": "sqlite"})
    controller.submit({"database_name": str(sqlite_path)})
    controller.submit(
        {
            "cdm_db_name": "warehouse_cdm",
            "schema_name": "main",
            "vocab_schema": "main",
            "results_schema": None,
        }
    )
    result = controller.apply()

    assert result.status.value == "applied"
    stack = load_stack_config_from_path(path)
    assert "warehouse" in stack.connections
    assert "warehouse_cdm" in stack.databases
    assert GroundworkersConfig.validate_candidate(stack).cdm_db == "warehouse_cdm"


# ---------------------------------------------------------------------------
# Dynamic discovery
# ---------------------------------------------------------------------------


def test_model_choices_come_from_live_discovery(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def discoverer(provider_kind, base_url, api_key):
        calls.append((provider_kind, base_url, api_key))
        return DISCOVERED_MODELS

    controller, _ = _controller(
        _stack_path(tmp_path),
        LLM_SETUP_TARGET,
        llm_setup_workflow,
        discoverer=discoverer,
    )
    controller.start()
    transition = controller.submit(
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": "k",
        }
    )

    assert calls == [("ollama", "http://localhost:11434/v1", "k")]
    choice = next(
        field
        for field in transition.snapshot.step.fields
        if field.key == "llm_model_choice"
    )
    assert [option.value for option in choice.choices] == list(DISCOVERED_MODELS)


def test_unreachable_provider_reports_an_issue_without_leaking_detail(
    tmp_path: Path,
) -> None:
    def failing(provider_kind, base_url, api_key):
        raise RuntimeError(
            f"connect to {base_url} failed with api_key={api_key} and password=hunter2"
        )

    controller, _ = _controller(
        _stack_path(tmp_path),
        LLM_SETUP_TARGET,
        llm_setup_workflow,
        discoverer=failing,
    )
    controller.start()
    transition = controller.submit(
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": "sk-secret",
        }
    )

    assert transition.issues
    rendered = " ".join(issue.message for issue in transition.issues)
    assert "discovered" in rendered.lower()
    for secret in ("sk-secret", "hunter2"):
        assert secret not in rendered


def test_provider_returning_no_models_is_reported(tmp_path: Path) -> None:
    controller, _ = _controller(
        _stack_path(tmp_path),
        LLM_SETUP_TARGET,
        llm_setup_workflow,
        discoverer=lambda *_args: (),
    )
    controller.start()
    transition = controller.submit(
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": None,
        }
    )

    assert transition.issues
    assert "no available models" in " ".join(
        issue.message for issue in transition.issues
    )


# ---------------------------------------------------------------------------
# Navigation, review redaction, and outcomes
# ---------------------------------------------------------------------------


def test_back_navigation_returns_to_the_provider_step(tmp_path: Path) -> None:
    controller, _ = _controller(_stack_path(tmp_path), LLM_SETUP_TARGET, llm_setup_workflow)
    start = controller.start()
    controller.submit(
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": None,
        }
    )

    back = controller.back()

    assert back.step.key == start.step.key
    assert back.can_back is False


def test_review_shows_only_the_fields_the_journey_changed(tmp_path: Path) -> None:
    """The diff must not bury real changes under package-default churn.

    `plan_configure` returns a validated candidate with every default materialised,
    so comparing it against a hand-written base reported each unset default as a
    change (`mcp.port: None -> 8000`). Both sides are now normalised identically.
    """
    path = _stack_path(tmp_path)
    service = _service(path)
    draft = service.begin(LLM_SETUP_TARGET, MutationOperation.CREATE)
    service.submit(
        draft,
        "provider",
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
        },
    )
    service.submit(draft, "model", {"llm_model_entry_name": "chat_model", "llm_model_choice": "second-model:v1"})

    entries = service.plan(draft).diff.entries

    changed = {entry.field for entry in entries}
    # Creating the chat journey creates two new named entries plus the reference
    # to them, so every field of those entries is genuinely new.
    assert changed == {
        "providers.chat_provider.provider",
        "providers.chat_provider.base_url",
        "models.chat_model.provider",
        "models.chat_model.model",
        "models.chat_model.embeddings",
        "models.chat_model.extended_thinking",
        "models.chat_model.structured_output",
        "models.chat_model.tool_use",
        "tools.groundworkers.llm_model_name",
    }
    # The point of the test: untouched package defaults stay out of the diff.
    assert not {field for field in changed if field.startswith("tools.groundworkers.mcp")}


def test_staging_the_current_values_produces_an_empty_diff(tmp_path: Path) -> None:
    """Idempotence: re-applying the stored answers is a no-op in the review."""
    path = _stack_path(tmp_path)
    controller, _ = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    _drive_llm_to_review(controller, api_key=None)
    controller.apply()

    service = _service(path)
    draft = service.begin(LLM_SETUP_TARGET, MutationOperation.UPDATE)
    service.submit(
        draft,
        "provider",
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
        },
    )
    service.submit(draft, "model", {"llm_model_entry_name": "chat_model", "llm_model_choice": "second-model:v1"})

    assert service.plan(draft).diff.entries == ()


def test_review_redacts_secret_fields(tmp_path: Path) -> None:
    controller, _ = _controller(_stack_path(tmp_path), LLM_SETUP_TARGET, llm_setup_workflow)
    review = _drive_llm_to_review(controller, api_key="super-secret").snapshot

    rendered = repr(review)
    assert "super-secret" not in rendered
    # The chosen model is safe to show; the credential is not.
    assert "second-model" in rendered


def test_cancel_is_distinct_from_an_apply_outcome(tmp_path: Path) -> None:
    path = _stack_path(tmp_path)
    before = path.read_text(encoding="utf-8")
    controller, _ = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    _drive_llm_to_review(controller)

    result = controller.cancel()

    assert result.status.value == "cancelled"
    assert path.read_text(encoding="utf-8") == before


def test_read_only_configuration_cannot_be_written(tmp_path: Path) -> None:
    from groundworkers.application.setup.models import (
        ConfigurationOwnership,
        OwnershipMode,
    )

    path = _stack_path(tmp_path)
    ownership = ConfigurationOwnership(
        mode=OwnershipMode.DERIVED_READ_ONLY,
        guidance="This configuration is managed outside the setup console.",
    )
    assert ownership.editable is False
    service = _service(path, ownership=ownership)

    capabilities = service.capabilities(LLM_SETUP_TARGET, MutationOperation.CREATE)

    assert capabilities.supported is False
    assert capabilities.reason == (
        "This configuration is managed outside the setup console."
    )
    with pytest.raises(ValueError):
        service.begin(LLM_SETUP_TARGET, MutationOperation.CREATE)


def test_concurrent_edit_is_reported_as_conflicted(tmp_path: Path) -> None:
    path = _stack_path(tmp_path)
    controller, _ = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    _drive_llm_to_review(controller)

    # Another writer changes the file after the plan was prepared.
    other = build_cdm_stack(schema_name="other")
    save_stack_config(other, path)
    other_bytes = path.read_text(encoding="utf-8")

    result = controller.apply()

    assert result.status.value == "conflicted"
    assert "changed before" in result.summary
    # The other writer's file survives untouched — no clobber.
    assert path.read_text(encoding="utf-8") == other_bytes


def test_apply_notifies_the_host_so_pages_can_refresh(tmp_path: Path) -> None:
    applied: list[bool] = []
    path = _stack_path(tmp_path)
    service = _service(path, on_applied=lambda: applied.append(True))
    controller = ConfigWizardController(
        llm_setup_workflow(MutationOperation.CREATE), service
    )
    _drive_llm_to_review(controller)

    assert controller.apply().status.value == "applied"
    assert applied == [True]


def test_embedding_model_name_is_never_reused_as_the_chat_model(
    tmp_path: Path,
) -> None:
    """Chat and embeddings stay separate references.

    Writing the chat journey must not touch `embedding_model_name`, and must not
    read the embedding model as a chat default.
    """
    path = _stack_path(tmp_path)
    controller, _ = _controller(path, LLM_SETUP_TARGET, llm_setup_workflow)
    _drive_llm_to_review(controller)
    controller.apply()

    stack = load_stack_config_from_path(path)
    saved = GroundworkersConfig.validate_candidate(stack)
    assert saved.llm_model_name == "chat_model"
    assert stack.models["chat_model"].model == "second-model:v1"
    assert saved.embedding_model_name is None


def test_review_diff_masks_secrets_by_declaration_not_by_field_name(
    tmp_path: Path,
) -> None:
    """The apply review is where a credential would surface to an operator.

    Masking is driven by oa-configurator's `Sensitive()` marker on the schema,
    not by a local list of secret-looking field names, so this asserts the
    secret is absent from the rendered diff rather than asserting which fields
    were classified.
    """
    path = _stack_path(tmp_path)
    service = _service(path)
    draft = service.begin(LLM_SETUP_TARGET, MutationOperation.CREATE)
    service.submit(
        draft,
        "provider",
        {
            "llm_provider_name": "chat_provider",
            "llm_provider_kind": "ollama",
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": "super-secret-key",
        },
    )
    service.submit(
        draft, "model", {"llm_model_entry_name": "chat_model", "llm_model_choice": "second-model:v1"}
    )

    diff = service.plan(draft).diff

    assert "super-secret-key" not in repr(diff)
    # The field is still shown as changed -- masked, not hidden.
    assert "providers.chat_provider.api_key" in {entry.field for entry in diff.entries}
