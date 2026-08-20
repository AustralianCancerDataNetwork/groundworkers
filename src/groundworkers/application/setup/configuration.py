"""Load and persist the authoritative oa-configurator 1.x stack."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from oa_configurator import (
    ConfigurationError,
    StackConfig,
    save_stack_config,
)
from oa_configurator import loader as _loader
from pydantic import ValidationError

from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationSaveResult,
    ConfigurationSnapshot,
    ConfigurationState,
    SetupIssue,
)
from groundworkers.config import GroundworkersConfig, split_vocabulary_connection


class ConfigurationConflictError(RuntimeError):
    """Raised when a save would overwrite configuration changed elsewhere."""


def resolved_config_path() -> Path:
    """The stack configuration the running process would load on its own.

    ``OA_CONFIG_PATH`` when set, otherwise ``~/.config/omop/config.toml``.

    Read through the module attribute rather than imported as a name, so this
    returns the same value ``load_stack_config`` will: oa-configurator resolves
    ``CONFIG_PATH`` once at import, and the setup console must not resolve a
    second, differently-timed answer. The console previously defaulted to
    ``DEFAULT_CONFIG_PATH``, which ignores the environment variable entirely --
    so with it set, the console edited one file and ``groundworkers serve`` read
    another.
    """

    return Path(_loader.CONFIG_PATH)


def missing_revision(path: str | Path) -> str:
    """Return the opaque revision representing an absent destination."""

    resolved = Path(path).expanduser().resolve()
    return "missing:" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()


def load_configuration(
    *,
    config_path: str | Path | None = None,
    ownership: ConfigurationOwnership | None = None,
) -> ConfigurationSnapshot:
    """Load setup configuration without constructing the runtime application."""

    path = Path(config_path or resolved_config_path()).expanduser().resolve()
    ownership = ownership or ConfigurationOwnership()
    if not path.exists():
        return ConfigurationSnapshot(
            state=ConfigurationState.MISSING,
            path=path,
            ownership=ownership,
            revision=missing_revision(path),
            issues=(
                SetupIssue(
                    code="config_missing",
                    field="config_path",
                    message="No stack configuration exists at the selected path.",
                ),
            ),
        )

    try:
        raw = path.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return ConfigurationSnapshot(
            state=ConfigurationState.MALFORMED,
            path=path,
            ownership=ownership,
            issues=(_load_issue(exc),),
        )

    revision = hashlib.sha256(raw).hexdigest()
    try:
        stack = StackConfig.model_validate(payload)
    except ValidationError as exc:
        return ConfigurationSnapshot(
            state=ConfigurationState.MALFORMED,
            path=path,
            ownership=ownership,
            revision=revision,
            issues=_validation_issues(exc),
        )

    stack.bind_loaded_path(path)
    issues = _incomplete_issues(stack)
    return ConfigurationSnapshot(
        state=(
            ConfigurationState.INCOMPLETE if issues else ConfigurationState.UNVERIFIED
        ),
        path=path,
        ownership=ownership,
        stack=stack,
        revision=revision,
        issues=issues,
    )


def save_configuration(
    stack: StackConfig,
    *,
    path: str | Path,
    expected_revision: str | None,
    ownership: ConfigurationOwnership,
) -> ConfigurationSaveResult:
    """Compare, validate, and atomically persist an authoritative stack."""

    if not ownership.editable:
        raise PermissionError(
            "This configuration is read-only; use its controlling source to edit it."
        )
    validated = StackConfig.model_validate(stack.model_dump(mode="python"))
    groundworkers = GroundworkersConfig.validate_candidate(validated)
    split = split_vocabulary_connection(validated, groundworkers.cdm_db)
    if split is not None:
        primary, vocabulary = split
        raise ValueError(
            f"The CDM database reads its vocabulary from connection {vocabulary!r} and "
            f"its clinical tables from {primary!r}. Groundworkers uses one connection "
            "for both; separate them with vocab_schema instead."
        )
    destination = Path(path).expanduser().resolve()
    existed = destination.exists()
    if existed:
        current_revision = hashlib.sha256(destination.read_bytes()).hexdigest()
        if expected_revision is None or current_revision != expected_revision:
            raise ConfigurationConflictError(
                "The configuration changed after it was opened; reload before saving."
            )
    elif expected_revision not in (None, missing_revision(destination)):
        raise ConfigurationConflictError(
            "The configuration path changed after it was opened; reload before saving."
        )

    save_stack_config(validated, destination)
    reloaded = load_configuration(
        config_path=destination,
        ownership=ownership,
    )
    if not reloaded.usable:
        raise RuntimeError("Saved configuration failed reload verification.")
    return ConfigurationSaveResult(
        snapshot=reloaded,
        backup_path=(
            destination.with_name(f"{destination.name}.bak") if existed else None
        ),
        replaced_existing=existed,
    )


def _incomplete_issues(stack: StackConfig) -> tuple[SetupIssue, ...]:
    try:
        groundworkers = GroundworkersConfig.validate_candidate(stack)
    except (ConfigurationError, ValidationError, ValueError) as exc:
        return (
            SetupIssue(
                code="groundworkers_config_incomplete",
                field="tools.groundworkers",
                message=(
                    "Groundworkers needs a valid CDM database reference before it can start. "
                    f"Validation failed with {type(exc).__name__}."
                ),
            ),
        )
    # Reference-valid but unrunnable: the runtime refuses this at bootstrap, so
    # the console must not present it as merely unverified.
    split = split_vocabulary_connection(stack, groundworkers.cdm_db)
    if split is not None:
        primary, vocabulary = split
        return (
            SetupIssue(
                code="vocabulary_connection_split",
                field=f"databases.{groundworkers.cdm_db}.vocab_connection",
                message=(
                    f"The CDM database reads its vocabulary from connection {vocabulary!r} "
                    f"and its clinical tables from {primary!r}. Groundworkers uses one "
                    "connection for both; separate them with vocab_schema instead."
                ),
            ),
        )
    return ()


def _validation_issues(exc: ValidationError) -> tuple[SetupIssue, ...]:
    issues = []
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        location = ".".join(str(part) for part in error["loc"]) or None
        issues.append(
            SetupIssue(
                code=f"validation_{error['type']}",
                field=location,
                message=str(error["msg"]),
            )
        )
    return tuple(issues)


def _load_issue(exc: Exception) -> SetupIssue:
    if isinstance(exc, tomllib.TOMLDecodeError):
        return SetupIssue(
            code="malformed_toml",
            message="The configuration is not valid TOML.",
        )
    if isinstance(exc, UnicodeError):
        return SetupIssue(
            code="invalid_encoding",
            message="The configuration must be UTF-8 text.",
        )
    return SetupIssue(
        code="config_unreadable",
        message="The configuration file could not be read.",
    )
