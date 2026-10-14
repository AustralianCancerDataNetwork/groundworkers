from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

_REQUIRED_TEST_MARKERS = {
    "GROUNDWORKERS_REQUIRE_INTEGRATION": "required_integration",
    "GROUNDWORKERS_REQUIRE_FORMAT_TESTS": "required_format",
}
_required_test_ids: dict[str, set[str]] = {}
_required_test_outcomes: dict[str, dict[str, str]] = {}

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolated_env_file(tmp_path_factory, monkeypatch):
    """Keep the recorded config location out of the developer's home directory.

    `groundworkers._env.ENV_FILE` is a real path under ~/.config/omop, and the
    location wizard writes it. Autouse rather than opt-in: a test that records a
    location and forgets to redirect it repoints the machine Groundworkers reads
    from, which is not a failure any assertion would catch.
    """
    from groundworkers import _env

    monkeypatch.setattr(
        _env, "ENV_FILE", tmp_path_factory.mktemp("env") / "groundworkers.env"
    )
    monkeypatch.delenv(_env.ENV_CONFIG_PATH, raising=False)
    # `load_environment` writes this module global directly, so a test that calls
    # it leaves every later test looking like it was launched with a broken
    # OA_CONFIG_PATH -- which makes the setup page open the location wizard.
    monkeypatch.setattr(_env, "_rejected", None)


def _missing_required_tests(expected: set[str], selected: set[str]) -> list[str]:
    return sorted(expected - selected)


def _required_test_failures(
    expected: set[str], outcomes: dict[str, str]
) -> list[str]:
    return sorted(nodeid for nodeid in expected if outcomes.get(nodeid) != "passed")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session: pytest.Session, items: list[pytest.Item]):
    enabled = {
        marker
        for variable, marker in _REQUIRED_TEST_MARKERS.items()
        if os.getenv(variable) == "1"
    }
    expected = {
        marker: {item.nodeid for item in items if item.get_closest_marker(marker)}
        for marker in enabled
    }
    yield
    selected_items = {item.nodeid for item in session.items}
    _required_test_ids.clear()
    _required_test_ids.update(expected)
    for marker in enabled:
        _required_test_outcomes[marker] = {}
    session.config._required_test_ids = expected
    session.config._required_test_collection_errors = {
        marker: _missing_required_tests(ids, selected_items)
        for marker, ids in expected.items()
    }
    session.config._required_test_outcomes = {
        marker: _required_test_outcomes.setdefault(marker, {}) for marker in enabled
    }


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when not in {"setup", "call", "teardown"}:
        return
    for marker, outcomes in _required_test_outcomes.items():
        if report.when == "call" or report.outcome != "passed":
            if report.nodeid in _required_test_ids.get(marker, set()):
                outcomes[report.nodeid] = report.outcome


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    required = getattr(session.config, "_required_test_ids", {})
    collection_errors = getattr(session.config, "_required_test_collection_errors", {})
    outcomes = getattr(session.config, "_required_test_outcomes", {})
    problems = {
        marker: sorted(
            set(collection_errors.get(marker, []))
            | set(_required_test_failures(ids, outcomes.get(marker, {})))
        )
        for marker, ids in required.items()
    }
    for marker, ids in required.items():
        if not ids:
            problems[marker].append(f"no tests collected with {marker} marker")
    failures = {marker: entries for marker, entries in problems.items() if entries}
    if not failures:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        for marker, entries in failures.items():
            reporter.write_sep("!", f"required {marker} execution gate failed")
            for nodeid in entries:
                outcome = outcomes.get(marker, {}).get(nodeid, "not executed or deselected")
                reporter.write_line(f"{nodeid}: {outcome}")
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
