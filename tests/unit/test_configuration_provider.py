from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest
from groundskeeping.configurator import (
    ConfigApplyIntent,
    ConfigApplyStatus,
    ConfigTarget,
    ConfigTargetKind,
    ConfigWizardController,
    MutationOperation,
)
from groundskeeping.configurator.conformance import (
    assert_mutation_service_conformance,
)
from groundskeeping.contracts import FormStep, ReviewStep, WizardResultStatus
from oa_configurator import (
    ConfigurationError,
    GenericDatabaseConfig,
    StackConfig,
    save_stack_config,
)

import groundworkers.application.setup.configuration_provider as provider_module
from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.configuration_provider import (
    CDM_SETUP_TARGET,
    GroundworkersConfigMutationService,
    cdm_setup_workflow,
    model_setup_workflow,
)
from groundworkers.config import GroundworkersConfig
from tests.support import build_cdm_stack

CDM_SUBMISSIONS = (
    (
        "connection",
        {
            "connection_name": "cdm_main",
            "dialect": "sqlite",
        },
    ),
    ("database", {"database_name": ":memory:"}),
    (
        "cdm",
        {
            "cdm_db_name": "cdm_db",
            "schema_name": "main",
            "vocab_schema": "main",
            "results_schema": None,
        },
    ),
)


def test_groundworkers_config_uses_real_1x_references() -> None:
    candidate = _cdm_stack()

    resolved = GroundworkersConfig.validate_candidate(candidate)

    assert resolved.cdm_db == "cdm_db"
    assert candidate.databases["cdm_db"].connection == "cdm_main"


def test_groundworkers_config_rejects_missing_and_wrong_kind_references() -> None:
    missing = StackConfig(tools={"groundworkers": {"cdm_db": "missing"}})
    wrong_kind = StackConfig(
        connections=_cdm_stack().connections,
        databases={
            "cdm_db": GenericDatabaseConfig(
                connection="cdm_main",
                schema_name="main",
            )
        },
        tools={"groundworkers": {"cdm_db": "cdm_db"}},
    )

    with pytest.raises(ConfigurationError, match="unknown database"):
        GroundworkersConfig.validate_candidate(missing)
    with pytest.raises(ConfigurationError, match="requires a CDMDatabaseConfig"):
        GroundworkersConfig.validate_candidate(wrong_kind)


def test_real_provider_passes_the_reusable_conformance_suite(tmp_path: Path) -> None:
    paths: list[Path] = []

    def factory() -> GroundworkersConfigMutationService:
        path = tmp_path / f"candidate-{len(paths)}.toml"
        paths.append(path)
        return GroundworkersConfigMutationService(path)

    assert_mutation_service_conformance(
        factory,
        CDM_SETUP_TARGET,
        MutationOperation.CREATE,
        CDM_SUBMISSIONS,
    )

    applied = load_configuration(config_path=paths[0])
    assert applied.usable
    assert applied.stack is not None
    assert applied.stack.tools["groundworkers"]["cdm_db"] == "cdm_db"


def test_generic_wizard_runs_multi_target_cdm_flow_without_early_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    controller = ConfigWizardController(
        cdm_setup_workflow(MutationOperation.CREATE),
        GroundworkersConfigMutationService(path),
    )

    review = _reach_cdm_review(controller)

    assert isinstance(review.step, ReviewStep)
    assert not path.exists()
    assert review.can_apply
    changes = {change.field for change in review.step.review.changes}
    assert "connections.cdm_main.dialect" in changes
    assert "databases.cdm_db.connection" in changes
    assert "tools.groundworkers.cdm_db" in changes
    assert {
        effect.destination_target.kind
        for effect in review.step.review.effects
        if effect.destination_target is not None
    } == {ConfigTargetKind.CONNECTION, ConfigTargetKind.DATABASE}

    result = controller.apply()

    assert result.status is WizardResultStatus.APPLIED
    assert result.refresh_pages == frozenset({"configuration", "database"})
    assert path.exists()


def test_provider_updates_an_existing_cdm_configuration(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_stack_config(_cdm_stack(), path)
    service = GroundworkersConfigMutationService(path)
    capabilities = service.capabilities(CDM_SETUP_TARGET, MutationOperation.UPDATE)
    controller = ConfigWizardController(
        cdm_setup_workflow(MutationOperation.UPDATE),
        service,
    )

    assert capabilities.supported
    controller.start()
    controller.submit({"connection_name": "cdm_main", "dialect": "sqlite"})
    controller.submit({"database_name": "updated.db"})
    review = controller.submit(
        {
            "cdm_db_name": "cdm_db",
            "schema_name": "clinical",
            "vocab_schema": "vocabulary",
            "results_schema": "results",
        }
    ).snapshot
    assert isinstance(review.step, ReviewStep)
    result = controller.apply()

    assert result.status is WizardResultStatus.APPLIED
    reloaded = load_configuration(config_path=path)
    assert reloaded.stack is not None
    assert reloaded.stack.connections["cdm_main"].database_name == "updated.db"
    assert reloaded.stack.databases["cdm_db"].schema_name == "clinical"


def test_model_discovery_refreshes_only_the_future_choice_and_keeps_secrets_safe(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "config.toml"
    save_stack_config(_cdm_stack(), path)
    calls: list[tuple[str, str | None, bool]] = []
    canary = "x1-provider-secret-canary"

    def discover(provider: str, base_url: str | None, api_key: str | None):
        calls.append((provider, base_url, api_key == canary))
        return ("embed-small", "embed-large")

    controller = ConfigWizardController(
        model_setup_workflow(MutationOperation.CREATE),
        GroundworkersConfigMutationService(path, model_discoverer=discover),
    )
    snapshots = [controller.start()]
    model = controller.submit(
        {
            "provider_name": "local_models",
            "provider_kind": "ollama",
            "base_url": "http://models.example",
            "api_key": canary,
        }
    ).snapshot
    snapshots.append(model)

    assert calls == [("ollama", "http://models.example", True)]
    assert isinstance(model.step, FormStep)
    assert model.step.key == "model"
    choice = next(field for field in model.step.fields if field.key == "model_choice")
    assert choice.disabled is False
    assert tuple(option.value for option in choice.choices) == (
        "embed-small",
        "embed-large",
    )
    assert model.values["model_choice"] == "embed-small"
    assert canary not in repr(snapshots)
    assert canary not in repr([asdict(snapshot) for snapshot in snapshots])

    review = controller.submit(
        {
            "model_entry_name": "embedding_model",
            "model_choice": "embed-small",
        }
    ).snapshot
    snapshots.append(review)
    assert isinstance(review.step, ReviewStep)
    assert canary not in repr(review)
    assert any(change.sensitive for change in review.step.review.changes)

    result = controller.apply()

    assert result.status is WizardResultStatus.APPLIED
    assert result.refresh_pages == frozenset({"configuration", "models"})
    assert canary not in repr(result)
    assert canary not in caplog.text
    assert canary in path.read_text(encoding="utf-8")


def test_stale_and_mismatched_apply_intents_are_consumed_and_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    service = GroundworkersConfigMutationService(path)
    draft, plan = _planned_cdm(service)

    wrong = service.apply(
        ConfigApplyIntent(
            ConfigTarget(ConfigTargetKind.TOOL, "other", "Other"),
            MutationOperation.CREATE,
            plan.apply_token or "",
            draft.expected_revision,
        )
    )
    reused = service.apply(
        ConfigApplyIntent(
            CDM_SETUP_TARGET,
            MutationOperation.CREATE,
            plan.apply_token or "",
            draft.expected_revision,
        )
    )
    assert wrong.status is ConfigApplyStatus.REJECTED
    assert reused.status is ConfigApplyStatus.REJECTED

    stale_service = GroundworkersConfigMutationService(path)
    stale_draft, stale_plan = _planned_cdm(stale_service)
    path.write_text("# another writer\n", encoding="utf-8")
    conflicted = stale_service.apply(
        ConfigApplyIntent(
            CDM_SETUP_TARGET,
            MutationOperation.CREATE,
            stale_plan.apply_token or "",
            stale_draft.expected_revision,
        )
    )
    stale_reuse = stale_service.apply(
        ConfigApplyIntent(
            CDM_SETUP_TARGET,
            MutationOperation.CREATE,
            stale_plan.apply_token or "",
            stale_draft.expected_revision,
        )
    )
    assert conflicted.status is ConfigApplyStatus.CONFLICTED
    assert stale_reuse.status is ConfigApplyStatus.REJECTED


def test_cancel_closes_the_private_candidate_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    service = GroundworkersConfigMutationService(path)
    draft, plan = _planned_cdm(service)

    service.cancel(draft)
    result = service.apply(
        ConfigApplyIntent(
            CDM_SETUP_TARGET,
            MutationOperation.CREATE,
            plan.apply_token or "",
            draft.expected_revision,
        )
    )

    assert result.status is ConfigApplyStatus.REJECTED
    assert not path.exists()


def test_generic_controller_cancel_returns_cancelled_without_writing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    controller = ConfigWizardController(
        cdm_setup_workflow(MutationOperation.CREATE),
        GroundworkersConfigMutationService(path),
    )
    controller.start()

    result = controller.cancel()

    assert result.status is WizardResultStatus.CANCELLED
    assert not path.exists()


def test_provider_rejects_invalid_and_unknown_submissions(tmp_path: Path) -> None:
    service = GroundworkersConfigMutationService(tmp_path / "config.toml")
    draft = service.begin(CDM_SETUP_TARGET, MutationOperation.CREATE)

    invalid = service.submit(
        draft,
        "connection",
        {"connection_name": "", "dialect": "unsupported"},
    )

    assert not invalid.accepted
    assert {issue.field_key for issue in invalid.issues} == {
        "connection_name",
        "dialect",
    }
    with pytest.raises(ValueError, match="Unknown configuration fields"):
        service.submit(draft, "connection", {"old_resource_name": "cdm"})


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (PermissionError("rejected-secret"), WizardResultStatus.REJECTED),
        (OSError("failed-secret"), WizardResultStatus.FAILED),
        (RuntimeError("reload-secret"), WizardResultStatus.FAILED),
    ),
)
def test_generic_controller_preserves_safe_rejected_and_failed_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected: WizardResultStatus,
) -> None:
    path = tmp_path / f"{expected.value}.toml"
    controller = ConfigWizardController(
        cdm_setup_workflow(MutationOperation.CREATE),
        GroundworkersConfigMutationService(path),
    )
    _reach_cdm_review(controller)

    def fail_save(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(provider_module, "save_configuration", fail_save)
    result = controller.apply()

    assert result.status is expected
    assert "secret" not in repr(result)
    assert "secret" not in caplog.text
    assert not path.exists()


def test_generic_controller_returns_actionable_conflict(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    controller = ConfigWizardController(
        cdm_setup_workflow(MutationOperation.CREATE),
        GroundworkersConfigMutationService(path),
    )
    _reach_cdm_review(controller)
    path.write_text("# concurrent writer\n", encoding="utf-8")

    result = controller.apply()

    assert result.status is WizardResultStatus.CONFLICTED
    assert "Reload" in str(result.detail)
    assert path.read_text(encoding="utf-8") == "# concurrent writer\n"


def _cdm_stack() -> StackConfig:
    return build_cdm_stack()


def _planned_cdm(service: GroundworkersConfigMutationService):
    draft = service.begin(CDM_SETUP_TARGET, MutationOperation.CREATE)
    for step, values in CDM_SUBMISSIONS:
        staged = service.submit(draft, step, values)
        draft = type(draft)(
            draft.target,
            draft.operation,
            draft.session_token,
            staged.changed_fields,
            draft.expected_revision,
        )
    return draft, service.plan(draft)


def _reach_cdm_review(controller: ConfigWizardController):
    first = controller.start()
    assert isinstance(first.step, FormStep)
    assert first.step.key == "connection"
    database = controller.submit(
        {"connection_name": "cdm_main", "dialect": "sqlite"}
    ).snapshot
    assert database.step.key == "database"
    cdm = controller.submit({"database_name": ":memory:"}).snapshot
    assert cdm.step.key == "cdm"
    return controller.submit(
        {
            "cdm_db_name": "cdm_db",
            "schema_name": "main",
            "vocab_schema": "main",
            "results_schema": None,
        }
    ).snapshot
