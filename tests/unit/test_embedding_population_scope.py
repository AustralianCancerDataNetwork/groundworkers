"""The scope an operator chooses before a population run starts."""

from __future__ import annotations

from groundskeeping.contracts import FormStep, ReviewStep

from groundworkers.application.setup.models import (
    CoverageScope,
    CoverageSnapshot,
    EmbeddingConfiguration,
    EmbeddingCoverageReport,
    EmbeddingIndexSnapshot,
    VocabularyCoverage,
)
from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.embeddings import EmbeddingPopulationWizardController

_VOCABULARIES = (
    # SNOMED and RxNorm still have concepts pending; LOINC is finished.
    VocabularyCoverage("SNOMED", 100, 40, 60, 40.0),
    VocabularyCoverage("RxNorm", 50, 0, 50, 0.0),
    VocabularyCoverage("LOINC", 20, 20, 0, 100.0),
)


def _configuration() -> EmbeddingConfiguration:
    return EmbeddingConfiguration(
        backend="pgvector",
        vector_store_name="embeddings",
        database_name="embedding_db",
        connection_name="cdm_main",
        database_safe_url="postgresql+psycopg://user:***@localhost:5432/db",
        provider_name="embedding_provider",
        provider_kind="ollama",
        model_entry_name="embedding_model",
        model_name="arctic:v1",
        embeddings_supported=True,
        api_base="http://localhost:11434/v1",
    )


def _report(*, standard_only: bool = True) -> EmbeddingCoverageReport:
    scope = CoverageScope(
        model_name="arctic:v1",
        metric="cosine",
        vocabularies=tuple(row.vocabulary for row in _VOCABULARIES),
        standard_only=standard_only,
        valid_only=False,
    )
    return EmbeddingCoverageReport(
        configuration=_configuration(),
        coverage=CoverageSnapshot(
            scope=scope,
            available=True,
            rows=_VOCABULARIES,
            eligible_total=170,
            embedded_total=60,
            pending_total=110,
        ),
        index=EmbeddingIndexSnapshot(model_name="arctic:v1", registered=True),
    )


def _controller(report: EmbeddingCoverageReport | None = None):
    session = SetupSession()
    return EmbeddingPopulationWizardController(
        session,
        coverage=report if report is not None else _report(),
        launcher=lambda command: None,
    )


def _command(controller) -> str:
    review = controller.review().snapshot
    assert isinstance(review.step, ReviewStep)
    return next(
        str(change.after)
        for change in review.step.review.changes
        if change.field == "command"
    )


def test_scope_is_the_first_thing_asked_and_lists_the_real_vocabularies() -> None:
    snapshot = _controller().start()

    assert isinstance(snapshot.step, FormStep)
    assert snapshot.step.key == "scope"
    modes = next(
        field for field in snapshot.step.fields if field.key == "vocabulary_mode"
    )
    assert tuple(option.value for option in modes.choices) == (
        "all",
        "incomplete",
        "selected",
    )
    # The count names how many are actually behind, not how many exist.
    assert "(2)" in dict(
        (option.value, option.label) for option in modes.choices
    )["incomplete"]
    listed = next(
        field for field in snapshot.step.fields if field.key == "vocabularies"
    )
    # Prefilled with what is missing: re-embedding a finished vocabulary is the
    # expensive mistake, so LOINC is not offered up by default.
    assert listed.default == "SNOMED\nRxNorm"


def test_named_vocabularies_reach_the_command_and_nothing_else_does() -> None:
    controller = _controller()
    controller.start()

    transition = controller.submit(
        {
            "concept_scope": "standard",
            "vocabulary_mode": "selected",
            "vocabularies": "RxNorm, SNOMED",
        }
    )

    assert not transition.issues
    command = _command(controller)
    assert "--vocabulary SNOMED" in command
    assert "--vocabulary RxNorm" in command
    assert "LOINC" not in command
    assert "--standard-only" in command


def test_a_vocabulary_the_cdm_does_not_have_is_named_back() -> None:
    """Silently dropping an unknown name would start a run that embeds less."""
    controller = _controller()
    controller.start()

    issues = controller.submit(
        {
            "concept_scope": "standard",
            "vocabulary_mode": "selected",
            "vocabularies": "SNOMED\nICD10\nNDC",
        }
    ).issues

    assert len(issues) == 1
    assert issues[0].field_key == "vocabularies"
    assert "ICD10" in issues[0].message
    assert "NDC" in issues[0].message
    assert "SNOMED" not in issues[0].message


def test_choosing_all_concepts_drops_the_standard_only_filter() -> None:
    controller = _controller()
    controller.start()

    controller.submit(
        {"concept_scope": "all", "vocabulary_mode": "all", "vocabularies": ""}
    )

    command = _command(controller)
    assert "--standard-only" not in command
    assert "--vocabulary" not in command


def test_incomplete_mode_runs_exactly_the_vocabularies_still_behind() -> None:
    controller = _controller()
    controller.start()

    controller.submit(
        {"concept_scope": "standard", "vocabulary_mode": "incomplete", "vocabularies": ""}
    )

    command = _command(controller)
    assert "--vocabulary SNOMED" in command
    assert "--vocabulary RxNorm" in command
    assert "LOINC" not in command


def test_changing_the_concept_scope_warns_that_the_counts_no_longer_apply() -> None:
    """Coverage was measured under one WHERE clause; the run uses another."""
    controller = _controller(_report(standard_only=True))
    controller.start()
    controller.submit(
        {"concept_scope": "all", "vocabulary_mode": "all", "vocabularies": ""}
    )

    review = controller.review().snapshot
    assert isinstance(review.step, ReviewStep)
    assert any(
        "do not describe this run" in warning
        for warning in review.step.review.warnings
    )

    unchanged = _controller(_report(standard_only=True))
    unchanged.start()
    unchanged.submit(
        {"concept_scope": "standard", "vocabulary_mode": "all", "vocabularies": ""}
    )
    assert not unchanged.review().snapshot.step.review.warnings


def test_selecting_nothing_is_refused_rather_than_run_as_everything() -> None:
    controller = _controller()
    controller.start()

    issues = controller.submit(
        {"concept_scope": "standard", "vocabulary_mode": "selected", "vocabularies": "  "}
    ).issues

    assert len(issues) == 1
    assert issues[0].field_key == "vocabularies"
