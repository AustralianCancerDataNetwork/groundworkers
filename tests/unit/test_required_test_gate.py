from __future__ import annotations

from tests.conftest import _missing_required_tests, _required_test_failures


def test_required_execution_gate_detects_a_marked_test_removed_from_selection():
    required = {"test_a", "test_b"}

    assert _missing_required_tests(required, {"test_a"}) == ["test_b"]


def test_required_execution_gate_rejects_skipped_or_unexecuted_cases():
    required = {"test_a", "test_b", "test_c"}
    outcomes = {"test_a": "passed", "test_b": "skipped"}

    assert _required_test_failures(required, outcomes) == ["test_b", "test_c"]
