from __future__ import annotations

import re
import tomllib
from collections import Counter
from collections.abc import Callable
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve()

LEGACY_API_DEBT: dict[str, dict[str, int]] = {
    "ResourceConfig": {},
    "ToolConfig": {},
    "EmbeddingClient": {},
    "ProviderType": {},
    "omop_emb.embeddings.embedding_client": {},
    "omop_emb.embeddings.embedding_providers": {},
    "SearchConstraintConcept": {},
    "omop_graph.graph.constraints": {},
    "set_embedding_client": {},
    # R5 deleted the duplicate ToolConfig-era LLM writer and its bespoke wizard;
    # every setup write goal now runs through the generic Groundskeeping flow.
    "setup.llm_configuration": {},
    "wizards.llm_provider": {},
    "apply_llm_configuration": {},
    # R5 removed the last cross-package internal config import.
    "OmopGraphConfig": {},
    "OmopEmbConfig": {},
}

LEGACY_FIXTURE_DEBT: dict[str, tuple[re.Pattern[str], dict[str, int]]] = {
    "resources section": (
        re.compile(r"\[resources\."),
        {},
    ),
    "resources argument": (
        re.compile(r"\bresources\s*="),
        {},
    ),
    "resource aliases": (
        re.compile(r"\[resource_aliases\]|\bresource_aliases\s*="),
        {},
    ),
    "profiles section": (
        re.compile(r"\[profiles\."),
        {},
    ),
    "profiles argument": (
        re.compile(r"\bprofiles\s*="),
        {},
    ),
    "active profile": (
        re.compile(r"\bactive_profile\b"),
        {},
    ),
    "default resource": (
        re.compile(r"\bdefault_resource\b"),
        {},
    ),
    "tool extra section": (
        re.compile(r"\[(?:profiles\.[^]]+\.)?tools\.[^]]+\.extra"),
        {},
    ),
}


def test_removed_api_debt_does_not_grow() -> None:
    python_files = _python_files(REPOSITORY / "src") + _python_files(
        REPOSITORY / "tests"
    )

    for token, expected in LEGACY_API_DEBT.items():
        observed = _literal_counts(python_files, token)
        assert observed == expected, _debt_message(token, expected, observed)


def test_legacy_stack_fixture_debt_does_not_grow() -> None:
    test_files = _python_files(REPOSITORY / "tests")

    for name, (pattern, expected) in LEGACY_FIXTURE_DEBT.items():
        observed = _pattern_counts(test_files, pattern)
        assert observed == expected, _debt_message(name, expected, observed)


def test_deleted_setup_write_modules_are_not_importable() -> None:
    """The duplicate pre-1.0 write flow was deleted, not ported.

    Lives here rather than with the write-flow tests so the module names can be
    named literally without registering as migration debt.
    """
    for module in (
        "groundworkers.tui.wizards.llm_provider",
        "groundworkers.application.setup.llm_configuration",
        "groundworkers.application.setup.database_configuration",
    ):
        try:
            __import__(module)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{module} should have been deleted by the migration.")


def test_migration_dependencies_use_explicit_public_boundaries() -> None:
    metadata = tomllib.loads(
        (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = metadata["project"]["dependencies"]

    assert "omop-llm>=1,<2" in dependencies

    # RELEASE BLOCKER. No source pins may survive into a publishable artifact.
    sources = metadata.get("tool", {}).get("uv", {}).get("sources", {})
    assert not sources


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        path for path in sorted(root.rglob("*.py")) if path.resolve() != THIS_FILE
    )


def _literal_counts(files: tuple[Path, ...], token: str) -> dict[str, int]:
    return _counts(files, lambda text: text.count(token))


def _pattern_counts(
    files: tuple[Path, ...],
    pattern: re.Pattern[str],
) -> dict[str, int]:
    return _counts(files, lambda text: len(pattern.findall(text)))


def _counts(
    files: tuple[Path, ...],
    count: Callable[[str], int],
) -> dict[str, int]:
    observed: Counter[str] = Counter()
    for path in files:
        occurrences = count(path.read_text(encoding="utf-8"))
        if occurrences:
            observed[str(path.relative_to(REPOSITORY))] = occurrences
    return dict(observed)


def _debt_message(
    name: str,
    expected: dict[str, int],
    observed: dict[str, int],
) -> str:
    return (
        f"Migration debt changed for {name!r}. New occurrences are forbidden; "
        "when existing occurrences are removed, shrink the allowlist in this test. "
        f"Expected {expected!r}, observed {observed!r}."
    )
