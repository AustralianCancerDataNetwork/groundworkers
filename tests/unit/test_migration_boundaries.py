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
    "EmbeddingClient": {
        "src/groundworkers/adapters/omop_emb.py": 5,
        "src/groundworkers/adapters/omop_graph.py": 6,
        "src/groundworkers/app.py": 4,
        "src/groundworkers/application/setup/embedding_setup.py": 2,
        "src/groundworkers/tools/system_tools.py": 1,
    },
    "ProviderType": {
        "src/groundworkers/app.py": 2,
        "src/groundworkers/application/setup/embedding_population.py": 2,
        "src/groundworkers/application/setup/embedding_setup.py": 5,
        "tests/unit/test_setup_embeddings.py": 2,
    },
    "omop_emb.embeddings.embedding_client": {
        "src/groundworkers/adapters/omop_emb.py": 1,
        "src/groundworkers/application/setup/embedding_setup.py": 1,
    },
    "omop_emb.embeddings.embedding_providers": {
        "src/groundworkers/application/setup/embedding_population.py": 1,
    },
}

LEGACY_FIXTURE_DEBT: dict[str, tuple[re.Pattern[str], dict[str, int]]] = {
    "resources section": (
        re.compile(r"\[resources\."),
        {
            "tests/unit/test_setup_embeddings.py": 4,
            "tests/unit/test_tui_app.py": 2,
        },
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
        {
            "tests/unit/test_setup_embeddings.py": 3,
            "tests/unit/test_tui_app.py": 2,
        },
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


def test_migration_dependencies_use_explicit_public_boundaries() -> None:
    metadata = tomllib.loads(
        (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = metadata["project"]["dependencies"]
    groundskeeping_source = metadata["tool"]["uv"]["sources"]["groundskeeping"]

    assert "omop-llm>=1,<2" in dependencies
    assert groundskeeping_source == {
        "git": "https://github.com/AustralianCancerDataNetwork/groundskeeping.git",
        "branch": "generic_write",
    }


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
