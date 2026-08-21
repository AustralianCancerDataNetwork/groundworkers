from __future__ import annotations

from groundworkers.application.setup.models import (
    ConnectionResult,
    DiagnosticSeverity,
    ResourceDiagnostic,
)
from groundworkers.application.setup.performance_maintenance import (
    PerformanceRemediation,
    build_performance_commands,
)
from groundworkers.tui.presenters.performance import PerformancePresenter


def _result(target_key: str, *codes: str) -> ConnectionResult:
    return ConnectionResult(
        target_key=target_key,
        connected=True,
        latency_ms=1.0,
        safe_url="postgresql://db",
        diagnostics=tuple(
            ResourceDiagnostic(code, "present", DiagnosticSeverity.INFO)
            for code in codes
        ),
    )


def test_performance_surface_collects_index_readiness() -> None:
    view = PerformancePresenter().landing(
        connections=(
            _result(
                "database.graph",
                "fulltext_indexes_present",
                "functional_indexes_present",
            ),
            _result("database.groundworkers", "trigram_indexes_present"),
        ),
        embedding_configuration=None,
        embedding_coverage=None,
        can_prepare=False,
    )

    assert view.title == "Performance"
    assert [row.cells for row in view.rows] == [
        ("Graph", "Full-text indexes", "Ready"),
        ("Graph", "Functional text indexes", "Ready"),
        ("Groundworkers", "Trigram indexes", "Ready"),
        ("Embeddings", "Vector index", "Not configured"),
    ]


def test_performance_commands_run_trigram_before_embedding_index() -> None:
    commands = build_performance_commands(
        (
            PerformanceRemediation.TRIGRAM_INDEXES,
            PerformanceRemediation.EMBEDDING_INDEX,
        ),
        embedding_model="ollama/qwen3-embedding:0.6b",
        config_path="/tmp/config.toml",
    )

    assert commands[0].argv[-1] == "trigram"
    assert commands[1].argv[1:3] == ("maintenance", "rebuild-index")
    assert "--index-type" in commands[1].argv
    assert all(command.environment == (("OA_CONFIG_PATH", "/tmp/config.toml"),) for command in commands)
