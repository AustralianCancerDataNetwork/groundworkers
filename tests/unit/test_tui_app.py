from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def test_groundworkers_spec_keeps_setup_as_a_registered_page() -> None:
    pytest.importorskip("groundskeeping")

    from groundworkers.tui.app import build_groundworkers_tui_spec

    spec = build_groundworkers_tui_spec(
        config_path="/definitely/not/a/groundworkers-config.toml",
    )

    assert spec.validate().keys() == ("setup",)
    assert spec.default_page == "setup"
    assert spec.title == "Groundworkers"


def test_groundworkers_pages_do_not_cover_workbench() -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    async def run_check() -> None:
        app = OperatorApp(
            build_groundworkers_tui_spec(
                config_path="/definitely/not/a/groundworkers-config.toml",
            )
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            active_page = app.query_one(".operator-page.-active")
            workbench = app.query_one("#workbench")
            catalogue_panel = app.query_one("#catalogue-panel")
            sections = app.query_one("#sections")
            catalogue = app.query_one("#catalogue")
            tabs = app.query_one("#page-tabs")

            assert active_page.region.height == 0
            assert workbench.region.height > 0
            assert catalogue_panel.region.height > 0
            assert tabs.styles.display == "none"
            assert sections.styles.display == "block"
            assert catalogue.styles.display == "none"
            assert sections.option_count == 5
            assert app.query_one("#result-panel").border_title == "Setup"
            assert app.query_one("#context-panel").border_title == "Database Setup"
            assert tuple(
                sections.get_option_at_index(index).id
                for index in range(sections.option_count)
            ) == (
                "setup.database",
                "setup.graph",
                "setup.llm_provider",
                "setup.embeddings",
                "setup.chat",
            )

            sections.focus()
            sections.highlighted = 2
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "LLM provider not configured" in str(
                app.query_one("#result-summary").render()
            )
            assert app.query_one("#result-panel").border_title == "Setup"
            assert app.query_one("#context-panel").border_title == "Model inventory"

            sections.highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            assert app.query_one("#result-panel").border_title == "Setup"
            assert app.query_one("#context-panel").border_title == "Graph Setup"
            assert app.query_one("#context").text == ""
            assert app.query_one("#context-table").styles.display == "none"

    asyncio.run(run_check())


def test_database_actions_render_in_workspace_and_verify_connections(
    tmp_path: Path,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            assert app.query_one("#catalogue").styles.display == "none"
            assert str(app.query_one("#view-action-0").label) == "Configure"
            assert str(app.query_one("#view-action-1").label) == "Test connections"
            assert str(app.query_one("#view-action-2").label) == "Refresh"

            await pilot.click("#view-action-1")
            await pilot.pause(0.2)

            table = app.query_one("#result-table")
            assert table.get_row_at(0)[3] == "Warnings"
            table.move_cursor(row=0, column=0, animate=False)
            await pilot.pause()
            context = app.query_one("#context-table")
            assert str(context.get_row_at(0)[0]) == "✓"
            assert str(context.get_row_at(1)[0]) == "!"
            assert "OMOP vocabulary tables are missing" in str(context.get_row_at(1)[1])

    asyncio.run(run_check())


def test_groundworkers_tuning_row_does_not_offer_configure(
    tmp_path: Path,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            table = app.query_one("#result-table")
            table.move_cursor(row=2, column=0, animate=False)
            await pilot.pause()

            assert table.get_row_at(2)[0] == "Groundworkers tuning"
            assert str(app.query_one("#view-action-0").label) == "Configure"
            assert app.query_one("#view-action-0").disabled is True
            assert str(app.query_one("#view-action-1").label) == "Test connections"
            assert str(app.query_one("#view-action-2").label) == "Refresh"

    asyncio.run(run_check())


def test_configure_action_opens_database_wizard(tmp_path: Path) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.click("#view-action-0")
            await pilot.pause()

            assert app.screen.query_one("#wizard-frame") is not None

    asyncio.run(run_check())


def test_llm_provider_wizard_renders_model_inventory_choices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp
    from textual.widgets import Select

    from groundworkers.application.setup.models import LlmProviderCheckResult
    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"

[tools.groundworkers.llm]
enabled = true
provider = "ollama"
api_base = "http://localhost:11434/v1"
default_model_name = "chat-model"
""",
        encoding="utf-8",
    )

    def fake_scan(draft):
        return LlmProviderCheckResult(
            provider=draft.provider,
            api_base=draft.api_base,
            default_model_name="chat-model",
            reachable=True,
            inventory=("chat-model", "other-model"),
        )

    monkeypatch.setattr(
        "groundworkers.tui.wizards.llm_provider.scan_llm_models",
        fake_scan,
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 2
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#view-action-0")
            await pilot.pause()
            await pilot.click("#wizard-next")
            await pilot.pause()

            choices = app.screen.query_one("#wizard-field-0", Select)
            assert choices.value == "chat-model"
            assert [value for _label, value in choices._options] == [
                "chat-model",
                "other-model",
            ]

    asyncio.run(run_check())


def test_llm_provider_detail_renders_model_inventory_table(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.application.setup.models import (
        LlmModelMetadata,
        LlmProviderCheckResult,
    )
    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"

[tools.groundworkers.llm]
enabled = true
provider = "ollama"
api_base = "http://localhost:11434/v1"
default_model_name = "chat-model"
""",
        encoding="utf-8",
    )

    def fake_verify(_snapshot):
        return LlmProviderCheckResult(
            provider="ollama",
            api_base="http://localhost:11434/v1",
            default_model_name="chat-model",
            reachable=True,
            model_available=True,
            inventory=("chat-model", "other-model"),
            model_metadata=(
                LlmModelMetadata(
                    name="chat-model",
                    size_bytes=4_294_967_296,
                    parameter_size="7B",
                    quantization_level="Q4_K_M",
                ),
            ),
        )

    monkeypatch.setattr(
        "groundworkers.tui.pages.setup.verify_llm_provider", fake_verify
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 2
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#view-action-1")
            await pilot.pause(0.2)

            detail = app.query_one("#context-table")
            assert detail.ordered_columns[0].label.plain == "Model"
            assert detail.ordered_columns[1].label.plain == "Size"
            assert detail.get_row_at(0)[0] == "chat-model"
            assert detail.get_row_at(0)[1] == "4.0 GB"
            assert detail.get_row_at(0)[2] == "7B"
            assert detail.get_row_at(1)[0] == "other-model"

    asyncio.run(run_check())


def test_embedding_coverage_refresh_shows_loading_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("groundskeeping")

    from time import sleep

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"

[tools.omop_emb.extra]
backend = "sqlitevec"
sqlite_path = "embeddings.db"
embedding_model = "test-model"
api_base = "http://localhost:11434/v1"
provider_type = "ollama"
""",
        encoding="utf-8",
    )

    def slow_refresh(*_args, **_kwargs):
        sleep(0.4)

    monkeypatch.setattr(
        "groundworkers.tui.pages.setup.load_embedding_coverage_report",
        slow_refresh,
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 3
            await pilot.press("enter")
            await pilot.pause()

            await pilot.click("#view-action-0")
            await pilot.pause(0.05)

            assert app.query_one("#result-loading").styles.display == "block"
            assert app.query_one("#result-table").styles.display == "none"
            assert "Counting CDM vocabularies" in str(
                app.query_one("#result-summary").render()
            )
            assert app.query_one("#context-panel").border_title == "Vocabulary coverage"

    asyncio.run(run_check())


def test_embedding_coverage_refresh_places_vocabularies_in_detail_pane(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.application.setup.models import (
        CoverageScope,
        CoverageSnapshot,
        EmbeddingConfiguration,
        EmbeddingCoverageReport,
        EmbeddingIndexSnapshot,
        VocabularyCoverage,
    )
    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"

[tools.omop_emb.extra]
backend = "sqlitevec"
sqlite_path = "embeddings.db"
embedding_model = "test-model"
api_base = "http://localhost:11434/v1"
provider_type = "ollama"
""",
        encoding="utf-8",
    )

    report = EmbeddingCoverageReport(
        configuration=EmbeddingConfiguration(
            backend="sqlitevec",
            provider_kind="ollama",
            model_name="test-model",
            api_base="http://localhost:11434/v1",
        ),
        coverage=CoverageSnapshot(
            scope=CoverageScope(
                model_name="test-model",
                metric="cosine",
                vocabularies=("SNOMED", "LOINC"),
                standard_only=True,
                valid_only=False,
            ),
            available=True,
            rows=(
                VocabularyCoverage("SNOMED", 10, 4, 6, 40.0),
                VocabularyCoverage("LOINC", 5, 5, 0, 100.0),
            ),
            eligible_total=15,
            embedded_total=9,
            pending_total=6,
        ),
        index=EmbeddingIndexSnapshot(
            model_name="test-model",
            registered=True,
            storage_identifier="emb_test",
            registry_index_type="flat",
        ),
    )

    monkeypatch.setattr(
        "groundworkers.tui.pages.setup.load_embedding_coverage_report",
        lambda *_args, **_kwargs: report,
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 3
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#view-action-0")
            await pilot.pause(0.2)

            setup = app.query_one("#result-table")
            assert setup.get_row_at(3)[0] == "Index"
            assert setup.get_row_at(3)[1] == "FLAT / exact scan"

            detail = app.query_one("#context-table")
            assert app.query_one("#context-panel").border_title == "Vocabulary coverage"
            assert detail.ordered_columns[0].label.plain == "Vocabulary"
            assert detail.get_row_at(0)[0] == "All vocabularies"
            assert detail.get_row_at(0)[3] == "6"
            assert detail.get_row_at(1)[0] == "SNOMED"
            assert detail.get_row_at(2)[0] == "LOINC"

    asyncio.run(run_check())
