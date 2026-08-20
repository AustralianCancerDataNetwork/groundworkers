from __future__ import annotations

from pathlib import Path

from groundworkers.application.setup.embedding_artifacts import (
    check_artifact_compatibility,
)
from groundworkers.application.setup.embedding_coverage import (
    calculate_coverage,
    load_coverage,
)
from groundworkers.application.setup.embedding_population import _coverage_blocker
from groundworkers.application.setup.models import ArtifactMetadata, CoverageScope


def _scope(**changes) -> CoverageScope:
    values = {
        "model_name": "test-model",
        "metric": "cosine",
        "vocabularies": ("SNOMED",),
        "standard_only": True,
        "valid_only": True,
    }
    values.update(changes)
    return CoverageScope(**values)


def test_compatible_prebuilt_artifact_matches_full_scope(tmp_path: Path) -> None:
    artifact = ArtifactMetadata(
        path=tmp_path / "starter.h5",
        model_name="test-model",
        dimensions=768,
        metric="cosine",
        provider="openai",
        row_count=100,
        vocabularies=("SNOMED", "LOINC"),
        standard_only=True,
        valid_only=True,
    )

    result = check_artifact_compatibility(artifact, scope=_scope(), dimensions=768)

    assert result.compatible is True


def test_artifact_without_filter_metadata_is_not_assumed_compatible(
    tmp_path: Path,
) -> None:
    artifact = ArtifactMetadata(
        path=tmp_path / "starter.h5",
        model_name="test-model",
        dimensions=768,
        metric="cosine",
        provider="openai",
        row_count=100,
    )

    result = check_artifact_compatibility(artifact, scope=_scope(), dimensions=768)

    assert result.compatible is False
    assert {issue.code for issue in result.issues} == {
        "artifact_scope_unknown",
        "artifact_filter_unknown",
    }


def test_filtered_scope_does_not_use_unfiltered_omop_emb_aggregate() -> None:
    class Backend:
        def get_embedding_count_by_vocabulary(self, **_kwargs):
            raise AssertionError("unfiltered aggregate must not be used")

    result = load_coverage(
        _scope(),
        backend=Backend(),
        eligible_counter=lambda _scope: {"SNOMED": 10},
    )

    assert result.available is False
    assert "cannot apply" in (result.blocker or "")


def test_unfiltered_coverage_uses_pushed_down_counts() -> None:
    class Backend:
        def get_embedding_count_by_vocabulary(self, **_kwargs):
            return (("SNOMED", "4"),)

    result = load_coverage(
        _scope(standard_only=False, valid_only=False),
        backend=Backend(),
        eligible_counter=lambda _scope: {"SNOMED": 10},
    )

    assert result.available is True
    assert result.rows[0].pending == 6
    assert result.rows[0].coverage_percent == 40.0


def test_mismatched_counts_are_rejected() -> None:
    result = calculate_coverage(
        _scope(), eligible={"SNOMED": 3}, embedded={"SNOMED": 4}
    )

    assert result.available is False
    assert "exceeds eligible" in (result.blocker or "")


def test_a_configuration_verdict_reaches_the_operator_intact() -> None:
    """A rejected model name is the answer, so it must not be reduced to a class name."""
    blocker = _coverage_blocker(
        ValueError(
            "Ollama model name 'arctic:latest' uses the mutable ':latest' tag."
        )
    )

    assert "':latest' tag" in blocker
    assert "arctic:latest" in blocker


def test_operational_failures_stay_class_named_and_urls_never_survive() -> None:
    """Drivers quote the DSN that failed; neither path may put one on screen."""
    operational = _coverage_blocker(
        OSError("could not connect to postgresql://user:hunter2@db.internal:5432/cdm")
    )

    assert operational.endswith("failed with OSError.")
    assert "hunter2" not in operational
    assert "db.internal" not in operational

    leaky = _coverage_blocker(
        ValueError("bad endpoint postgresql://user:hunter2@db.internal:5432/cdm here")
    )

    assert "hunter2" not in leaky
    assert "bad endpoint" in leaky
