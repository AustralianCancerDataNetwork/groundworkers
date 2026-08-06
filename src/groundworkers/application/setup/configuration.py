from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import tomllib

from oa_configurator import (
    DEFAULT_CONFIG_PATH,
    Resolver,
    StackConfig,
    save_stack_config,
)
from omop_emb.config import BackendType, OmopEmbConfig
from pydantic import ValidationError

from groundworkers.application.setup.models import (
    ConfigurationOwnership,
    ConfigurationSaveResult,
    ConfigurationSnapshot,
    ConfigurationState,
    SetupIssue,
)
from groundworkers.config import (
    resolve_cdm_resource_name,
    resolve_embedding_resource_name,
)


def load_configuration(
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
    ownership: ConfigurationOwnership | None = None,
) -> ConfigurationSnapshot:
    """Load setup configuration without constructing the runtime application."""

    path = Path(config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    ownership = ownership or ConfigurationOwnership()
    if not path.exists():
        return ConfigurationSnapshot(
            state=ConfigurationState.MISSING,
            path=path,
            profile=profile,
            ownership=ownership,
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
            profile=profile,
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
            profile=profile,
            ownership=ownership,
            revision=revision,
            issues=_validation_issues(exc),
        )

    stack.bind_loaded_path(path)
    if profile is not None:
        stack.active_profile = profile

    issues = _incomplete_issues(stack)
    return ConfigurationSnapshot(
        state=(
            ConfigurationState.INCOMPLETE if issues else ConfigurationState.UNVERIFIED
        ),
        path=path,
        profile=stack.active_profile,
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
    """Validate and atomically persist an authoritative stack configuration."""

    if not ownership.editable:
        raise PermissionError(
            "This configuration is read-only; use its controlling source to edit it."
        )
    validated = StackConfig.model_validate(stack.model_dump(mode="python"))
    destination = Path(path).expanduser().resolve()
    existed = destination.exists()
    if existed:
        current_revision = hashlib.sha256(destination.read_bytes()).hexdigest()
        if expected_revision is None or current_revision != expected_revision:
            raise RuntimeError(
                "The configuration changed after it was opened; reload before saving."
            )
    elif expected_revision is not None:
        raise RuntimeError(
            "The configuration path no longer exists; reload before saving."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_path = (
        destination.with_suffix(f"{destination.suffix}.bak") if existed else None
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        save_stack_config(validated, temporary_path)
        os.chmod(temporary_path, 0o600)
        with temporary_path.open("rb") as written:
            os.fsync(written.fileno())
        if backup_path is not None:
            shutil.copy2(destination, backup_path)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    reloaded = load_configuration(
        config_path=destination,
        profile=validated.active_profile,
        ownership=ownership,
    )
    if not reloaded.usable:
        if backup_path is not None:
            shutil.copy2(backup_path, destination)
        raise RuntimeError("Saved configuration failed reload verification.")
    return ConfigurationSaveResult(
        snapshot=reloaded,
        backup_path=backup_path,
        replaced_existing=existed,
    )


def _incomplete_issues(stack: StackConfig) -> tuple[SetupIssue, ...]:
    issues: list[SetupIssue] = []
    if stack.active_profile is not None and stack.active_profile not in stack.profiles:
        issues.append(
            SetupIssue(
                code="active_profile_missing",
                field="active_profile",
                message=f"Active profile {stack.active_profile!r} is not defined.",
            )
        )
        return tuple(issues)

    resolver = Resolver(stack)
    try:
        cdm_resource_name = resolve_cdm_resource_name(stack)
        resolver.resolve_resource(cdm_resource_name)
    except Exception:  # noqa: BLE001 - converted to a stable setup issue
        issues.append(
            SetupIssue(
                code="cdm_resource_unresolved",
                field="resources",
                message="A CDM resource with CDM and vocabulary schemas is required.",
            )
        )

    tool = _effective_tool(stack, OmopEmbConfig.tool_name)
    if tool is None:
        return tuple(issues)

    try:
        embedding_config = OmopEmbConfig.from_stack(stack)
    except (KeyError, TypeError, ValueError, ValidationError):
        issues.append(
            SetupIssue(
                code="embedding_config_invalid",
                field="tools.omop_emb",
                message="The embedding tool configuration is incomplete or invalid.",
            )
        )
        return tuple(issues)

    try:
        backend = BackendType(embedding_config.backend)
    except ValueError:
        if str(embedding_config.backend).lower() == "faiss":
            message = (
                "FAISS is a cache accelerator, not an embedding backend. "
                "Set backend to 'sqlitevec' or 'pgvector' and configure faiss_cache_dir instead."
            )
        else:
            message = "The embedding backend is not supported by this installation."
        issues.append(
            SetupIssue(
                code="embedding_backend_invalid",
                field="tools.omop_emb.extra.backend",
                message=message,
            )
        )
        return tuple(issues)

    if backend is BackendType.SQLITEVEC and not embedding_config.sqlite_path:
        issues.append(
            SetupIssue(
                code="embedding_path_missing",
                field="tools.omop_emb.extra.sqlite_path",
                message="The sqlite-vec backend requires an embedding database path.",
            )
        )
    if backend is BackendType.PGVECTOR:
        try:
            resolver.resolve_resource(resolve_embedding_resource_name(stack))
        except Exception:  # noqa: BLE001 - converted to a stable setup issue
            issues.append(
                SetupIssue(
                    code="embedding_resource_unresolved",
                    field="resources",
                    message="The pgvector backend requires a resolvable embedding resource.",
                )
            )
    return tuple(issues)


def _effective_tool(stack: StackConfig, name: str):
    if stack.active_profile and stack.active_profile in stack.profiles:
        profile_tool = stack.profiles[stack.active_profile].tools.get(name)
        if profile_tool is not None:
            return profile_tool
    return stack.tools.get(name)


def _validation_issues(exc: ValidationError) -> tuple[SetupIssue, ...]:
    issues = []
    for error in exc.errors(include_input=False, include_url=False):
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
