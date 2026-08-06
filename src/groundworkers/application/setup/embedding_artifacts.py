from __future__ import annotations

from pathlib import Path
from typing import Iterable

import h5py
from omop_emb.storage.embedding_bundle import ExportMetadata

from groundworkers.application.setup.models import (
    ArtifactCompatibility,
    ArtifactDiscovery,
    ArtifactMetadata,
    CoverageScope,
    SetupIssue,
)


def discover_embedding_artifacts(paths: Iterable[str | Path]) -> ArtifactDiscovery:
    """Inspect omop-emb bundles without importing or registering them."""

    artifacts: list[ArtifactMetadata] = []
    issues: list[SetupIssue] = []
    for path in _candidate_paths(paths):
        try:
            artifacts.append(_inspect_bundle(path))
        except Exception:  # noqa: BLE001 - corrupt files become typed discovery issues
            issues.append(
                SetupIssue(
                    code="artifact_unreadable",
                    field=str(path),
                    message="The prebuilt embedding artefact could not be inspected.",
                )
            )
    return ArtifactDiscovery(artifacts=tuple(artifacts), issues=tuple(issues))


def check_artifact_compatibility(
    artifact: ArtifactMetadata,
    *,
    scope: CoverageScope,
    dimensions: int | None,
) -> ArtifactCompatibility:
    issues: list[SetupIssue] = []
    if artifact.model_name != scope.model_name:
        issues.append(
            _issue("artifact_model_mismatch", "model_name", "Model names differ.")
        )
    if artifact.metric != scope.metric:
        issues.append(
            _issue("artifact_metric_mismatch", "metric", "Distance metrics differ.")
        )
    if dimensions is not None and artifact.dimensions != dimensions:
        issues.append(
            _issue(
                "artifact_dimension_mismatch",
                "dimensions",
                "Vector dimensions differ.",
            )
        )
    if artifact.vocabularies is None:
        issues.append(
            _issue(
                "artifact_scope_unknown",
                "vocabularies",
                "The artefact does not declare its vocabulary coverage.",
            )
        )
    elif not set(scope.vocabularies).issubset(artifact.vocabularies):
        issues.append(
            _issue(
                "artifact_vocabulary_mismatch",
                "vocabularies",
                "The artefact does not cover every selected vocabulary.",
            )
        )
    if artifact.standard_only is None or artifact.valid_only is None:
        issues.append(
            _issue(
                "artifact_filter_unknown",
                "filters",
                "The artefact does not declare its standard and validity filters.",
            )
        )
    elif (
        artifact.standard_only != scope.standard_only
        or artifact.valid_only != scope.valid_only
    ):
        issues.append(
            _issue(
                "artifact_filter_mismatch",
                "filters",
                "The artefact filters differ from the selected coverage scope.",
            )
        )
    return ArtifactCompatibility(compatible=not issues, issues=tuple(issues))


def _inspect_bundle(path: Path) -> ArtifactMetadata:
    with h5py.File(path, "r") as bundle:
        metadata = ExportMetadata.from_h5_attrs(bundle.attrs)
        vocabularies = _optional_csv(bundle.attrs.get("groundworkers_vocabularies"))
        standard_only = _optional_bool(bundle.attrs.get("groundworkers_standard_only"))
        valid_only = _optional_bool(bundle.attrs.get("groundworkers_valid_only"))
    return ArtifactMetadata(
        path=path,
        model_name=metadata.model_name,
        dimensions=metadata.dimensions,
        metric=metadata.metric_type.value,
        provider=metadata.provider_type.value,
        row_count=metadata.row_count,
        vocabularies=vocabularies,
        standard_only=standard_only,
        valid_only=valid_only,
    )


def _candidate_paths(paths: Iterable[str | Path]):
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            yield from sorted(path.glob("*.h5"))
        elif path.suffix.lower() in {".h5", ".hdf5"}:
            yield path


def _optional_csv(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes"}


def _issue(code: str, field: str, message: str) -> SetupIssue:
    return SetupIssue(code=code, field=field, message=message)
